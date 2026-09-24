"""Render one self-contained CloudShell launcher from reviewed sources. No AWS calls."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BEDROCK_PROFILE = "au.anthropic.claude-haiku-4-5-20251001-v1:0"
BEDROCK_MODEL = "anthropic.claude-haiku-4-5-20251001-v1:0"
SUPPORTED_BEDROCK_REGIONS = ("ap-southeast-2", "ap-southeast-4")


def ref(name):
    return {"Ref": name}


def sub(value):
    return {"Fn::Sub": value}


def att(name, field):
    return {"Fn::GetAtt": [name, field]}


def template():
    tags = [
        {"Key": "Project", "Value": "Threadly"},
        {"Key": "Environment", "Value": "staging"},
        {"Key": "Name", "Value": ref("AWS::StackName")},
    ]
    az = {"Fn::Select": [0, {"Fn::GetAZs": ""}]}
    resources = {}

    def resource(name, kind, props, **extra):
        resources[name] = {"Type": kind, "Properties": props, **extra}

    resource(
        "Vpc",
        "AWS::EC2::VPC",
        {
            "CidrBlock": "10.77.0.0/16",
            "EnableDnsSupport": True,
            "EnableDnsHostnames": True,
            "Tags": tags,
        },
    )
    resource("InternetGateway", "AWS::EC2::InternetGateway", {"Tags": tags})
    resource(
        "GatewayAttachment",
        "AWS::EC2::VPCGatewayAttachment",
        {
            "VpcId": ref("Vpc"),
            "InternetGatewayId": ref("InternetGateway"),
        },
    )
    resource(
        "Subnet",
        "AWS::EC2::Subnet",
        {
            "VpcId": ref("Vpc"),
            "CidrBlock": "10.77.1.0/24",
            "AvailabilityZone": az,
            "MapPublicIpOnLaunch": False,
            "Tags": tags,
        },
    )
    resource("RouteTable", "AWS::EC2::RouteTable", {"VpcId": ref("Vpc"), "Tags": tags})
    resource(
        "DefaultRoute",
        "AWS::EC2::Route",
        {
            "RouteTableId": ref("RouteTable"),
            "DestinationCidrBlock": "0.0.0.0/0",
            "GatewayId": ref("InternetGateway"),
        },
        DependsOn="GatewayAttachment",
    )
    resource(
        "RouteAssociation",
        "AWS::EC2::SubnetRouteTableAssociation",
        {
            "SubnetId": ref("Subnet"),
            "RouteTableId": ref("RouteTable"),
        },
    )
    resource(
        "SecurityGroup",
        "AWS::EC2::SecurityGroup",
        {
            "GroupDescription": "Threadly HTTPS only; administration through SSM",
            "VpcId": ref("Vpc"),
            "Tags": tags,
            "SecurityGroupIngress": [
                {
                    "IpProtocol": "tcp",
                    "FromPort": 443,
                    "ToPort": 443,
                    "CidrIp": "0.0.0.0/0",
                }
            ],
            # Outbound access is required for SSM, apt, Google and Bedrock.
            "SecurityGroupEgress": [{"IpProtocol": "-1", "CidrIp": "0.0.0.0/0"}],
        },
    )
    resource(
        "BackupBucket",
        "AWS::S3::Bucket",
        {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            },
            "BucketEncryption": {
                "ServerSideEncryptionConfiguration": [
                    {"ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}
                ]
            },
            "VersioningConfiguration": {"Status": "Enabled"},
            "LifecycleConfiguration": {
                "Rules": [
                    {
                        "Id": "ExpireStagingBackups",
                        "Status": "Enabled",
                        "ExpirationInDays": 14,
                        "NoncurrentVersionExpiration": {"NoncurrentDays": 7},
                    }
                ]
            },
            "Tags": tags,
        },
        DeletionPolicy="RetainExceptOnCreate",
        UpdateReplacePolicy="Retain",
    )
    resource(
        "BackupBucketPolicy",
        "AWS::S3::BucketPolicy",
        {
            "Bucket": ref("BackupBucket"),
            "PolicyDocument": {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Deny",
                        "Principal": "*",
                        "Action": "s3:*",
                        "Resource": [
                            att("BackupBucket", "Arn"),
                            sub("${BackupBucket.Arn}/*"),
                        ],
                        "Condition": {"Bool": {"aws:SecureTransport": "false"}},
                    }
                ],
            },
        },
    )
    resource(
        "InstanceRole",
        "AWS::IAM::Role",
        {
            "AssumeRolePolicyDocument": {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "ec2.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                    }
                ],
            },
            "ManagedPolicyArns": [
                sub(
                    "arn:${AWS::Partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
                )
            ],
            "Policies": [
                {
                    "PolicyName": "ThreadlyStaging",
                    "PolicyDocument": {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "bedrock:InvokeModel",
                                    "bedrock:InvokeModelWithResponseStream",
                                    "bedrock:GetInferenceProfile",
                                ],
                                "Resource": sub(
                                    "arn:${AWS::Partition}:bedrock:${AWS::Region}:"
                                    "${AWS::AccountId}:inference-profile/"
                                    + BEDROCK_PROFILE
                                ),
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "bedrock:InvokeModel",
                                    "bedrock:InvokeModelWithResponseStream",
                                ],
                                "Resource": [
                                    sub(
                                        "arn:${AWS::Partition}:bedrock:"
                                        + region
                                        + "::foundation-model/"
                                        + BEDROCK_MODEL
                                    )
                                    for region in SUPPORTED_BEDROCK_REGIONS
                                ],
                                "Condition": {
                                    "StringEquals": {
                                        "bedrock:InferenceProfileArn": sub(
                                            "arn:${AWS::Partition}:bedrock:${AWS::Region}:"
                                            "${AWS::AccountId}:inference-profile/"
                                            + BEDROCK_PROFILE
                                        )
                                    }
                                },
                            },
                            {
                                "Effect": "Allow",
                                "Action": "s3:ListBucket",
                                "Resource": att("BackupBucket", "Arn"),
                            },
                            {
                                "Effect": "Allow",
                                "Action": ["s3:PutObject", "s3:GetObject"],
                                "Resource": sub("${BackupBucket.Arn}/*"),
                            },
                        ],
                    },
                }
            ],
            "Tags": tags,
        },
        Condition="CreateRole",
    )
    resource(
        "InstanceProfile",
        "AWS::IAM::InstanceProfile",
        {
            "Roles": [ref("InstanceRole")],
        },
        Condition="CreateRole",
    )
    resource(
        "DataVolume",
        "AWS::EC2::Volume",
        {
            "AvailabilityZone": az,
            "Size": 50,
            "VolumeType": "gp3",
            "Encrypted": True,
            "Iops": 3000,
            "Throughput": 125,
            "Tags": tags,
        },
        DeletionPolicy="RetainExceptOnCreate",
        UpdateReplacePolicy="Retain",
    )
    resource(
        "LaunchTemplate",
        "AWS::EC2::LaunchTemplate",
        {
            "LaunchTemplateData": {
                "ImageId": ref("ImageId"),
                "InstanceType": ref("InstanceType"),
                "IamInstanceProfile": {
                    "Name": {
                        "Fn::If": [
                            "CreateRole",
                            ref("InstanceProfile"),
                            ref("ExistingInstanceProfile"),
                        ]
                    }
                },
                "MetadataOptions": {
                    "HttpEndpoint": "enabled",
                    "HttpTokens": "required",
                    "HttpPutResponseHopLimit": 2,
                },
                "CreditSpecification": {"CpuCredits": "standard"},
                # Stack termination protection guards routine deletion. Instance-level
                # protection would prevent CloudFormation rolling back a failed create.
                "InstanceInitiatedShutdownBehavior": "stop",
                "BlockDeviceMappings": [
                    {
                        "DeviceName": ref("RootDeviceName"),
                        "Ebs": {
                            "VolumeSize": 30,
                            "VolumeType": "gp3",
                            "Encrypted": True,
                            "DeleteOnTermination": True,
                            "Iops": 3000,
                            "Throughput": 125,
                        },
                    }
                ],
                "NetworkInterfaces": [
                    {
                        "DeviceIndex": 0,
                        "AssociatePublicIpAddress": True,
                        "SubnetId": ref("Subnet"),
                        "Groups": [ref("SecurityGroup")],
                    }
                ],
                "TagSpecifications": [
                    {"ResourceType": "instance", "Tags": tags},
                    {"ResourceType": "volume", "Tags": tags},
                ],
                "UserData": {"Fn::Base64": sub((ROOT / "user-data.sh").read_text())},
            }
        },
    )
    resource(
        "Instance",
        "AWS::EC2::Instance",
        {
            "LaunchTemplate": {
                "LaunchTemplateId": ref("LaunchTemplate"),
                "Version": att("LaunchTemplate", "LatestVersionNumber"),
            },
            "Tags": tags,
        },
        DependsOn=["DefaultRoute", "RouteAssociation"],
    )
    resource(
        "DataAttachment",
        "AWS::EC2::VolumeAttachment",
        {
            "Device": "/dev/sdf",
            "InstanceId": ref("Instance"),
            "VolumeId": ref("DataVolume"),
        },
    )
    resource(
        "ElasticIp",
        "AWS::EC2::EIP",
        {"Domain": "vpc", "Tags": tags},
        DependsOn="GatewayAttachment",
    )
    resource(
        "ElasticIpAssociation",
        "AWS::EC2::EIPAssociation",
        {
            "AllocationId": att("ElasticIp", "AllocationId"),
            "InstanceId": ref("Instance"),
        },
    )
    return {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Description": "Threadly staging host only. No live app, model invocation or Google writes.",
        "Parameters": {
            "ImageId": {"Type": "AWS::EC2::Image::Id"},
            "RootDeviceName": {"Type": "String", "Default": "/dev/sda1"},
            "InstanceType": {
                "Type": "String",
                "Default": "t3.large",
                "AllowedValues": ["t3.large", "t3.medium"],
            },
            "ExistingInstanceProfile": {
                "Type": "String",
                "Default": "",
                "AllowedPattern": "[A-Za-z0-9+=,.@_-]*",
            },
            "AutoStopHours": {
                "Type": "Number",
                "Default": 8,
                "MinValue": 1,
                "MaxValue": 12,
            },
        },
        "Conditions": {
            "CreateRole": {"Fn::Equals": [ref("ExistingInstanceProfile"), ""]}
        },
        "Resources": resources,
        "Outputs": {
            "InstanceId": {"Value": ref("Instance")},
            "PublicIp": {"Value": ref("ElasticIp")},
            "DataVolumeId": {"Value": ref("DataVolume")},
            "BackupBucket": {"Value": ref("BackupBucket")},
            "Region": {"Value": ref("AWS::Region")},
            "SessionManagerUrl": {
                "Value": sub(
                    "https://${AWS::Region}.console.aws.amazon.com/systems-manager/session-manager/${Instance}?region=${AWS::Region}"
                )
            },
        },
    }


def render():
    body = json.dumps(template(), indent=2) + "\n"
    (ROOT / "stack.json").write_text(body)
    launcher = (
        (ROOT / "launcher-prefix.sh").read_text()
        + body
        + (ROOT / "launcher-suffix.sh").read_text()
    )
    (ROOT / "cloudshell.sh").write_text(launcher)


if __name__ == "__main__":
    render()
