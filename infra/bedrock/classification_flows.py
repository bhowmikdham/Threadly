#!/usr/bin/env python3
"""Provision the selected Haiku 4.5 classification Flow; never activate the application.

Run with the backend dependencies installed, AWS_PROFILE set, and --output pointing
outside the checkout. --render-only makes no AWS calls and uses a synthetic account.
"""

import argparse
import os
import sys
from pathlib import Path

import cloudshell_flows as provision

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.classification.flow_release import bundle, caller_policy  # noqa: E402
from app.classification.flows import (  # noqa: E402
    HAIKU,
    REGION,
    ClassificationFlow,
    models,
    prompt_variant,
    verify_target,
)
from app.workflows.bedrock_flows import _definition_equal  # noqa: E402


class Control:
    """Use the same verifier as runtime against CLI read-only responses."""

    def __init__(self, aws):
        self.aws = aws

    def get_flow_alias(self, **kwargs):
        return self.aws("bedrock-agent", "get-flow-alias", kwargs)

    def get_flow_version(self, **kwargs):
        return self.aws("bedrock-agent", "get-flow-version", kwargs)

    def get_prompt(self, **kwargs):
        return self.aws("bedrock-agent", "get-prompt", kwargs)


def ensure_prompt(aws, candidate, model):
    variant = prompt_variant(model)
    fingerprint = provision.digest(variant)
    name = "threadly-classification-" + candidate.replace("_", "-") + "-" + fingerprint[:12]
    tags = {"ManagedBy": "threadly-classification", "PromptHash": fingerprint}
    summaries = aws("bedrock-agent", "list-prompts")["promptSummaries"]
    matches = [p for p in summaries if p["name"] == name]
    if len(matches) > 1:
        raise RuntimeError("Ambiguous managed prompt name.")
    if matches:
        arn = matches[0]["arn"]
        actual_tags = aws("bedrock-agent", "list-tags-for-resource", {"resourceArn": arn})["tags"]
        if any(actual_tags.get(k) != v for k, v in tags.items()):
            raise RuntimeError("Prompt ownership mismatch.")
    else:
        arn = aws("bedrock-agent", "create-prompt", {
            "name": name, "description": "Threadly classification " + fingerprint,
            "defaultVariant": "classify", "variants": [variant], "tags": tags,
            "clientToken": fingerprint,
        })["arn"]
    actual = aws("bedrock-agent", "get-prompt", {"promptIdentifier": arn})
    if actual.get("defaultVariant") != "classify" or actual.get("variants") != [variant]:
        raise RuntimeError("Managed prompt drift; refusing to overwrite it.")
    versions = aws("bedrock-agent", "list-prompts", {"promptIdentifier": arn})["promptSummaries"]
    description = "classification-" + fingerprint
    matches = [p for p in versions if p.get("description") == description
               and p.get("version") != "DRAFT"]
    if len(matches) > 1:
        raise RuntimeError("Ambiguous managed prompt versions.")
    if matches:
        return matches[0]["arn"]
    return aws("bedrock-agent", "create-prompt-version", {
        "promptIdentifier": arn, "description": description, "tags": tags,
        "clientToken": provision.digest({"prompt": arn, "variant": variant}),
    })["arn"]


def ensure_flow(aws, name, graph, role, tags):
    # Graph refers to a numbered managed CHAT prompt; no DRAFT prompt may run.
    existing = aws("bedrock-agent", "list-flows")["flowSummaries"]
    matches = [f for f in existing if f["name"] == name]
    if len(matches) > 1:
        raise RuntimeError("Ambiguous Flow name; inspect before continuing.")
    if matches:
        arn = matches[0]["arn"]
        actual_tags = aws("bedrock-agent", "list-tags-for-resource", {"resourceArn": arn})["tags"]
        if any(actual_tags.get(k) != v for k, v in tags.items()):
            raise RuntimeError("Flow ownership/release mismatch; refusing to modify it.")
    else:
        arn = aws("bedrock-agent", "create-flow", {
            "name": name, "description": "Threadly classification candidate; backend validated.",
            "executionRoleArn": role, "definition": graph, "tags": tags,
            "clientToken": provision.digest({"name": name, "graph": graph, "role": role}),
        })["arn"]
    current = aws("bedrock-agent", "get-flow", {"flowIdentifier": arn})
    if (current["executionRoleArn"] != role
            or not _definition_equal(current.get("definition", {}), graph)):
        raise RuntimeError("Flow definition/role drift; refusing to overwrite it.")
    return arn


def publish(aws, arn, role, model, graph, tags):
    prepared = provision.prepare_flow(aws, arn, graph, role)
    description = "classification-" + provision.digest(graph)
    # AWS CLI handles pagination; descriptions/tokens make interrupted runs resumable.
    versions = aws("bedrock-agent", "list-flow-versions", {"flowIdentifier": arn})
    matches = [v for v in versions["flowVersionSummaries"]
               if v.get("description") == description]
    if len(matches) > 1:
        raise RuntimeError("Ambiguous published versions; inspect before continuing.")
    if matches:
        version = matches[0]["version"]
    else:
        version = aws("bedrock-agent", "create-flow-version", {
            "flowIdentifier": arn, "description": description,
            "clientToken": provision.digest({"flow": arn, "definition": graph}),
        })["version"]
    aliases = aws("bedrock-agent", "list-flow-aliases", {"flowIdentifier": arn})
    matches = [a for a in aliases["flowAliasSummaries"] if a["name"] == "candidate-v1"]
    if len(matches) > 1:
        raise RuntimeError("Ambiguous candidate aliases; inspect before continuing.")
    if matches:
        alias = matches[0]["id"]
    else:
        alias = aws("bedrock-agent", "create-flow-alias", {
            "flowIdentifier": arn, "name": "candidate-v1",
            "description": "Evaluation candidate; backend activation is separate.",
            "routingConfiguration": [{"flowVersion": version}], "tags": tags,
            "clientToken": provision.digest({"flow": arn, "version": version, "alias": "v1"}),
        })["id"]
    target = ClassificationFlow(flow_arn=arn, alias_id=alias, version=version,
                                model_profile_arn=model, execution_role_arn=role,
                                prompt_arn=graph["nodes"][1]["configuration"]["prompt"][
                                    "sourceConfiguration"]["resource"]["promptArn"],
                                definition_hash=prepared["definition_sha256"])
    verify_target(Control(aws), target)
    return target


def run(args, aws=None):
    aws = aws or provision.Aws()
    os.umask(0o077)
    if args.render_only:
        account = "123456789012"
    else:
        account = aws("sts", "get-caller-identity")["Account"]
        profile = aws("bedrock", "get-inference-profile",
                      {"inferenceProfileIdentifier": "au." + HAIKU})
        provision.validate_profile(profile, account)
    args.output.mkdir(parents=True, exist_ok=True)
    prompts = {}
    for key, model in models(account).items():
        prompts[key] = (f"arn:aws:bedrock:{REGION}:{account}:prompt/HAIKU12345:1"
                        if args.render_only else ensure_prompt(aws, key, model))
        provision.write_json(args.output / "prompts.json", prompts)
    template, graphs, fingerprint, tags = bundle(account, prompts)
    if len(provision.canonical(template).encode()) > 51200:
        raise RuntimeError("Template exceeds inline CloudFormation limit.")
    stack = "threadly-classification-" + fingerprint[:12]
    manifest = {"stack_name": stack, "region": REGION, "release_hash": fingerprint,
                "status": "render_only" if args.render_only else "provisioning",
                "backend_enabled": False, "live_eval": "not_run", "candidates": {},
                "selected_candidate": "haiku",
                "prompts": prompts,
                "resources": {}}
    provision.write_json(args.output / "template.json", template)
    for key, graph in graphs.items():
        provision.write_json(args.output / (key + ".flow.json"), graph)
    provision.write_json(args.output / "manifest.json", manifest)
    print(f"Stack: {stack}\nAssets: {args.output}", flush=True)
    if args.render_only or getattr(args, "prepare_assets", False):
        if not args.render_only:
            manifest["status"] = "prompts_published_roles_and_flows_pending"
            provision.write_json(args.output / "manifest.json", manifest)
        return manifest
    try:
        aws("cloudformation", "validate-template", {"TemplateBody": provision.canonical(template)})
        outputs = provision.ensure_stack(aws, stack, template, tags)
        manifest["role_outputs"] = outputs
        for key, graph in graphs.items():
            model = models(account)[key]
            arn = ensure_flow(aws, stack + "-" + key.replace("_", "-"), graph,
                              outputs["HaikuRoleArn"], tags)
            manifest["resources"][key] = arn
            provision.write_json(args.output / "manifest.json", manifest)
            target = publish(aws, arn, outputs["HaikuRoleArn"], model, graph, tags)
            manifest["candidates"][key] = target.model_dump()
            provision.write_json(args.output / (key + ".target.json"), target.model_dump())
            provision.write_json(args.output / (key + ".caller-policy.json"), caller_policy(target))
            provision.write_json(args.output / "manifest.json", manifest)
            print(f"Published and verified {key}: {target.flow_arn} version {target.version}", flush=True)
        manifest["status"] = "published_selected_model_not_enabled"
    except Exception:
        manifest["status"] = "incomplete"
        raise
    finally:
        provision.write_json(args.output / "manifest.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--render-only", action="store_true")
    mode.add_argument("--prepare-assets", action="store_true",
                      help="Publish prompts and render real-account assets for validation")
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
