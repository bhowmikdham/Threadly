#!/usr/bin/env bash
# Generated cloudshell.sh includes this prefix, stack.json, and launcher-suffix.sh.
set -Eeuo pipefail
export AWS_PAGER=""

REGION="${THREADLY_REGION:-ap-southeast-2}"
STACK="${THREADLY_STACK:-threadly-staging}"
INSTANCE_TYPE="${THREADLY_INSTANCE_TYPE:-t3.large}"
INSTANCE_PROFILE="${THREADLY_INSTANCE_PROFILE:-}"
AUTO_STOP_HOURS="${THREADLY_AUTO_STOP_HOURS:-8}"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "$REGION" =~ ^[a-z]{2}-[a-z]+-[0-9]+$ ]] || die "Invalid AWS region."
[[ "$STACK" =~ ^[A-Za-z][A-Za-z0-9-]{0,127}$ ]] || die "Invalid stack name."
[[ "$INSTANCE_TYPE" == t3.large || "$INSTANCE_TYPE" == t3.medium ]] || die "Use t3.large or t3.medium."
[[ "$INSTANCE_PROFILE" =~ ^[A-Za-z0-9+=,.@_-]*$ ]] || die "Invalid instance profile name."
[[ "$AUTO_STOP_HOURS" =~ ^([1-9]|1[0-2])$ ]] || die "Auto-stop must be 1 to 12 whole hours."
command -v aws >/dev/null || die "Run this in AWS CloudShell; AWS CLI is required."
command -v python3 >/dev/null || die "Python 3 is required."

WORKDIR=$(mktemp -d "$HOME/threadly-launch.XXXXXX")
chmod 700 "$WORKDIR"
printf 'Region: %s | Stack: %s | Files: %s\n' "$REGION" "$STACK" "$WORKDIR"
aws sts get-caller-identity --region "$REGION" --query '{Account:Account,Identity:Arn}' --output table

# An existing stack is never updated, replaced or started by this launcher.
if aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" --output json > "$WORKDIR/existing.json" 2> "$WORKDIR/describe-error.txt"; then
    python3 - "$WORKDIR/existing.json" <<'PY'
import json, sys
stack = json.load(open(sys.argv[1]))["Stacks"][0]
if not any(t["Key"] == "Project" and t["Value"] == "Threadly" for t in stack.get("Tags", [])):
    sys.exit("Stack name is already used by another project; choose THREADLY_STACK.")
print("Existing stack:", stack["StackStatus"])
for output in stack.get("Outputs", []):
    print(output["OutputKey"] + ": " + output["OutputValue"])
if stack["StackStatus"] not in {"CREATE_COMPLETE", "UPDATE_COMPLETE"}:
    sys.exit("Inspect this stack in CloudFormation; it is not ready. No changes made.")
PY
    printf 'No changes made. Use EC2 Start/Stop for this server; do not create another stack.\n'
    exit 0
else
    # Distinguish a missing stack from AccessDenied, expired credentials or network failures.
    python3 - "$WORKDIR/describe-error.txt" "$STACK" <<'PY'
import sys
error = open(sys.argv[1]).read()
if "(ValidationError)" not in error or f"Stack with id {sys.argv[2]} does not exist" not in error:
    sys.exit(error)
PY
fi

if [[ -n "$INSTANCE_PROFILE" ]]; then
    aws iam get-instance-profile --instance-profile-name "$INSTANCE_PROFILE" --region "$REGION" --query InstanceProfile.Arn --output text
    printf 'Reusing profile %s. Its SSM, Bedrock and backup permissions must already be configured.\n' "$INSTANCE_PROFILE"
fi

IMAGE_ID=$(aws ssm get-parameter --region "$REGION" --name /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id --query Parameter.Value --output text)
aws ec2 describe-images --region "$REGION" --image-ids "$IMAGE_ID" --output json > "$WORKDIR/image.json"
ROOT_DEVICE=$(python3 - "$WORKDIR/image.json" <<'PY'
import json, sys
images = json.load(open(sys.argv[1]))["Images"]
if len(images) != 1:
    sys.exit("Expected exactly one Ubuntu AMI.")
image = images[0]
expected = {"OwnerId": "099720109477", "Architecture": "x86_64", "State": "available",
            "VirtualizationType": "hvm", "RootDeviceType": "ebs"}
if any(image.get(k) != v for k, v in expected.items()):
    sys.exit("AMI verification failed: expected Canonical Ubuntu, x86_64, available, HVM, EBS.")
print(image["RootDeviceName"])
PY
)
python3 - "$WORKDIR/parameters.json" "$IMAGE_ID" "$ROOT_DEVICE" "$INSTANCE_TYPE" "$INSTANCE_PROFILE" "$AUTO_STOP_HOURS" <<'PY'
import json, sys
keys = ["ImageId", "RootDeviceName", "InstanceType", "ExistingInstanceProfile", "AutoStopHours"]
with open(sys.argv[1], "w") as handle:
    json.dump([{"ParameterKey": key, "ParameterValue": value} for key, value in zip(keys, sys.argv[2:])], handle)
PY

cat > "$WORKDIR/stack.json" <<'THREADLY_CLOUDFORMATION'
{
  "AWSTemplateFormatVersion": "2010-09-09",
  "Description": "Threadly staging host only. No live app, model invocation or Google writes.",
  "Parameters": {
    "ImageId": {
      "Type": "AWS::EC2::Image::Id"
    },
    "RootDeviceName": {
      "Type": "String",
      "Default": "/dev/sda1"
    },
    "InstanceType": {
      "Type": "String",
      "Default": "t3.large",
      "AllowedValues": [
        "t3.large",
        "t3.medium"
      ]
    },
    "ExistingInstanceProfile": {
      "Type": "String",
      "Default": "",
      "AllowedPattern": "[A-Za-z0-9+=,.@_-]*"
    },
    "AutoStopHours": {
      "Type": "Number",
      "Default": 8,
      "MinValue": 1,
      "MaxValue": 12
    }
  },
  "Conditions": {
    "CreateRole": {
      "Fn::Equals": [
        {
          "Ref": "ExistingInstanceProfile"
        },
        ""
      ]
    }
  },
  "Resources": {
    "Vpc": {
      "Type": "AWS::EC2::VPC",
      "Properties": {
        "CidrBlock": "10.77.0.0/16",
        "EnableDnsSupport": true,
        "EnableDnsHostnames": true,
        "Tags": [
          {
            "Key": "Project",
            "Value": "Threadly"
          },
          {
            "Key": "Environment",
            "Value": "staging"
          },
          {
            "Key": "Name",
            "Value": {
              "Ref": "AWS::StackName"
            }
          }
        ]
      }
    },
    "InternetGateway": {
      "Type": "AWS::EC2::InternetGateway",
      "Properties": {
        "Tags": [
          {
            "Key": "Project",
            "Value": "Threadly"
          },
          {
            "Key": "Environment",
            "Value": "staging"
          },
          {
            "Key": "Name",
            "Value": {
              "Ref": "AWS::StackName"
            }
          }
        ]
      }
    },
    "GatewayAttachment": {
      "Type": "AWS::EC2::VPCGatewayAttachment",
      "Properties": {
        "VpcId": {
          "Ref": "Vpc"
        },
        "InternetGatewayId": {
          "Ref": "InternetGateway"
        }
      }
    },
    "Subnet": {
      "Type": "AWS::EC2::Subnet",
      "Properties": {
        "VpcId": {
          "Ref": "Vpc"
        },
        "CidrBlock": "10.77.1.0/24",
        "AvailabilityZone": {
          "Fn::Select": [
            0,
            {
              "Fn::GetAZs": ""
            }
          ]
        },
        "MapPublicIpOnLaunch": false,
        "Tags": [
          {
            "Key": "Project",
            "Value": "Threadly"
          },
          {
            "Key": "Environment",
            "Value": "staging"
          },
          {
            "Key": "Name",
            "Value": {
              "Ref": "AWS::StackName"
            }
          }
        ]
      }
    },
    "RouteTable": {
      "Type": "AWS::EC2::RouteTable",
      "Properties": {
        "VpcId": {
          "Ref": "Vpc"
        },
        "Tags": [
          {
            "Key": "Project",
            "Value": "Threadly"
          },
          {
            "Key": "Environment",
            "Value": "staging"
          },
          {
            "Key": "Name",
            "Value": {
              "Ref": "AWS::StackName"
            }
          }
        ]
      }
    },
    "DefaultRoute": {
      "Type": "AWS::EC2::Route",
      "Properties": {
        "RouteTableId": {
          "Ref": "RouteTable"
        },
        "DestinationCidrBlock": "0.0.0.0/0",
        "GatewayId": {
          "Ref": "InternetGateway"
        }
      },
      "DependsOn": "GatewayAttachment"
    },
    "RouteAssociation": {
      "Type": "AWS::EC2::SubnetRouteTableAssociation",
      "Properties": {
        "SubnetId": {
          "Ref": "Subnet"
        },
        "RouteTableId": {
          "Ref": "RouteTable"
        }
      }
    },
    "SecurityGroup": {
      "Type": "AWS::EC2::SecurityGroup",
      "Properties": {
        "GroupDescription": "Threadly HTTPS only; administration through SSM",
        "VpcId": {
          "Ref": "Vpc"
        },
        "Tags": [
          {
            "Key": "Project",
            "Value": "Threadly"
          },
          {
            "Key": "Environment",
            "Value": "staging"
          },
          {
            "Key": "Name",
            "Value": {
              "Ref": "AWS::StackName"
            }
          }
        ],
        "SecurityGroupIngress": [
          {
            "IpProtocol": "tcp",
            "FromPort": 443,
            "ToPort": 443,
            "CidrIp": "0.0.0.0/0"
          }
        ],
        "SecurityGroupEgress": [
          {
            "IpProtocol": "-1",
            "CidrIp": "0.0.0.0/0"
          }
        ]
      }
    },
    "BackupBucket": {
      "Type": "AWS::S3::Bucket",
      "Properties": {
        "PublicAccessBlockConfiguration": {
          "BlockPublicAcls": true,
          "BlockPublicPolicy": true,
          "IgnorePublicAcls": true,
          "RestrictPublicBuckets": true
        },
        "BucketEncryption": {
          "ServerSideEncryptionConfiguration": [
            {
              "ServerSideEncryptionByDefault": {
                "SSEAlgorithm": "AES256"
              }
            }
          ]
        },
        "VersioningConfiguration": {
          "Status": "Enabled"
        },
        "LifecycleConfiguration": {
          "Rules": [
            {
              "Id": "ExpireStagingBackups",
              "Status": "Enabled",
              "ExpirationInDays": 14,
              "NoncurrentVersionExpiration": {
                "NoncurrentDays": 7
              }
            }
          ]
        },
        "Tags": [
          {
            "Key": "Project",
            "Value": "Threadly"
          },
          {
            "Key": "Environment",
            "Value": "staging"
          },
          {
            "Key": "Name",
            "Value": {
              "Ref": "AWS::StackName"
            }
          }
        ]
      },
      "DeletionPolicy": "RetainExceptOnCreate",
      "UpdateReplacePolicy": "Retain"
    },
    "BackupBucketPolicy": {
      "Type": "AWS::S3::BucketPolicy",
      "Properties": {
        "Bucket": {
          "Ref": "BackupBucket"
        },
        "PolicyDocument": {
          "Version": "2012-10-17",
          "Statement": [
            {
              "Effect": "Deny",
              "Principal": "*",
              "Action": "s3:*",
              "Resource": [
                {
                  "Fn::GetAtt": [
                    "BackupBucket",
                    "Arn"
                  ]
                },
                {
                  "Fn::Sub": "${BackupBucket.Arn}/*"
                }
              ],
              "Condition": {
                "Bool": {
                  "aws:SecureTransport": "false"
                }
              }
            }
          ]
        }
      }
    },
    "InstanceRole": {
      "Type": "AWS::IAM::Role",
      "Properties": {
        "AssumeRolePolicyDocument": {
          "Version": "2012-10-17",
          "Statement": [
            {
              "Effect": "Allow",
              "Principal": {
                "Service": "ec2.amazonaws.com"
              },
              "Action": "sts:AssumeRole"
            }
          ]
        },
        "ManagedPolicyArns": [
          {
            "Fn::Sub": "arn:${AWS::Partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
          }
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
                    "bedrock:InvokeModelWithResponseStream"
                  ],
                  "Resource": {
                    "Fn::Sub": "arn:${AWS::Partition}:bedrock:${AWS::Region}::foundation-model/*"
                  }
                },
                {
                  "Effect": "Allow",
                  "Action": "s3:ListBucket",
                  "Resource": {
                    "Fn::GetAtt": [
                      "BackupBucket",
                      "Arn"
                    ]
                  }
                },
                {
                  "Effect": "Allow",
                  "Action": [
                    "s3:PutObject",
                    "s3:GetObject"
                  ],
                  "Resource": {
                    "Fn::Sub": "${BackupBucket.Arn}/*"
                  }
                }
              ]
            }
          }
        ],
        "Tags": [
          {
            "Key": "Project",
            "Value": "Threadly"
          },
          {
            "Key": "Environment",
            "Value": "staging"
          },
          {
            "Key": "Name",
            "Value": {
              "Ref": "AWS::StackName"
            }
          }
        ]
      },
      "Condition": "CreateRole"
    },
    "InstanceProfile": {
      "Type": "AWS::IAM::InstanceProfile",
      "Properties": {
        "Roles": [
          {
            "Ref": "InstanceRole"
          }
        ]
      },
      "Condition": "CreateRole"
    },
    "DataVolume": {
      "Type": "AWS::EC2::Volume",
      "Properties": {
        "AvailabilityZone": {
          "Fn::Select": [
            0,
            {
              "Fn::GetAZs": ""
            }
          ]
        },
        "Size": 50,
        "VolumeType": "gp3",
        "Encrypted": true,
        "Iops": 3000,
        "Throughput": 125,
        "Tags": [
          {
            "Key": "Project",
            "Value": "Threadly"
          },
          {
            "Key": "Environment",
            "Value": "staging"
          },
          {
            "Key": "Name",
            "Value": {
              "Ref": "AWS::StackName"
            }
          }
        ]
      },
      "DeletionPolicy": "RetainExceptOnCreate",
      "UpdateReplacePolicy": "Retain"
    },
    "LaunchTemplate": {
      "Type": "AWS::EC2::LaunchTemplate",
      "Properties": {
        "LaunchTemplateData": {
          "ImageId": {
            "Ref": "ImageId"
          },
          "InstanceType": {
            "Ref": "InstanceType"
          },
          "IamInstanceProfile": {
            "Name": {
              "Fn::If": [
                "CreateRole",
                {
                  "Ref": "InstanceProfile"
                },
                {
                  "Ref": "ExistingInstanceProfile"
                }
              ]
            }
          },
          "MetadataOptions": {
            "HttpEndpoint": "enabled",
            "HttpTokens": "required",
            "HttpPutResponseHopLimit": 2
          },
          "CreditSpecification": {
            "CpuCredits": "standard"
          },
          "InstanceInitiatedShutdownBehavior": "stop",
          "BlockDeviceMappings": [
            {
              "DeviceName": {
                "Ref": "RootDeviceName"
              },
              "Ebs": {
                "VolumeSize": 30,
                "VolumeType": "gp3",
                "Encrypted": true,
                "DeleteOnTermination": true,
                "Iops": 3000,
                "Throughput": 125
              }
            }
          ],
          "NetworkInterfaces": [
            {
              "DeviceIndex": 0,
              "AssociatePublicIpAddress": true,
              "SubnetId": {
                "Ref": "Subnet"
              },
              "Groups": [
                {
                  "Ref": "SecurityGroup"
                }
              ]
            }
          ],
          "TagSpecifications": [
            {
              "ResourceType": "instance",
              "Tags": [
                {
                  "Key": "Project",
                  "Value": "Threadly"
                },
                {
                  "Key": "Environment",
                  "Value": "staging"
                },
                {
                  "Key": "Name",
                  "Value": {
                    "Ref": "AWS::StackName"
                  }
                }
              ]
            },
            {
              "ResourceType": "volume",
              "Tags": [
                {
                  "Key": "Project",
                  "Value": "Threadly"
                },
                {
                  "Key": "Environment",
                  "Value": "staging"
                },
                {
                  "Key": "Name",
                  "Value": {
                    "Ref": "AWS::StackName"
                  }
                }
              ]
            }
          ],
          "UserData": {
            "Fn::Base64": {
              "Fn::Sub": "#!/bin/bash\nset -Eeuo pipefail\nexec > >(tee -a /var/log/threadly-bootstrap.log) 2>&1\ntrap 'echo \"Threadly bootstrap failed at line $LINENO; inspect /var/log/threadly-bootstrap.log\"' ERR\n\n# A cost guard is installed before package/network setup. Each boot starts a new timer.\ncat >/etc/systemd/system/threadly-autostop.service <<'UNIT'\n[Unit]\nDescription=Stop Threadly staging EC2 after the working session\n[Service]\nType=oneshot\nExecStart=/usr/sbin/shutdown -h now\nUNIT\ncat >/etc/systemd/system/threadly-autostop.timer <<'UNIT'\n[Unit]\nDescription=Threadly staging session limit\n[Timer]\nOnActiveSec=${AutoStopHours}h\nAccuracySec=1min\nUnit=threadly-autostop.service\n[Install]\nWantedBy=timers.target\nUNIT\nsystemctl daemon-reload\nsystemctl enable --now threadly-autostop.timer\n\n# Only the exact newly-created, owned data volume may be formatted.\n# Nitro exposes EBS volumes as NVMe disks, not necessarily /dev/sdf.\nTARGET_SERIAL=$(printf '%s' '${DataVolume}' | tr -d '-')\nDATA_DEVICE=''\nfor attempt in $(seq 1 120); do\n  DATA_DEVICE=$(lsblk -dn -o PATH,SERIAL | awk -v serial=\"$TARGET_SERIAL\" '$2 == serial {print $1}')\n  [ -n \"$DATA_DEVICE\" ] && break\n  sleep 3\ndone\n[ -n \"$DATA_DEVICE\" ] && [ -b \"$DATA_DEVICE\" ]\nFSTYPE=$(blkid -s TYPE -o value \"$DATA_DEVICE\" || true)\nif [ -z \"$FSTYPE\" ]; then\n  # Refuse a partitioned disk; this stack creates a blank standalone data volume.\n  [ \"$(lsblk -nr -o TYPE \"$DATA_DEVICE\" | wc -l)\" -eq 1 ]\n  mkfs.ext4 -L threadly-data \"$DATA_DEVICE\"\nelse\n  [ \"$FSTYPE\" = ext4 ]\nfi\nDATA_UUID=$(blkid -s UUID -o value \"$DATA_DEVICE\")\ninstall -d -m 0755 /srv/threadly-data\nif ! grep -q \"UUID=$DATA_UUID \" /etc/fstab; then\n  printf 'UUID=%s /srv/threadly-data ext4 defaults,nofail,x-systemd.device-timeout=60 0 2\\n' \"$DATA_UUID\" >>/etc/fstab\nfi\nmount /srv/threadly-data\nmountpoint -q /srv/threadly-data\n\nexport DEBIAN_FRONTEND=noninteractive\napt-get -o Acquire::Retries=3 update\napt-get -o Acquire::Retries=3 install -y ca-certificates curl git python3 unzip\ninstall -m 0755 -d /etc/apt/keyrings\ncurl --fail --silent --show-error --location --retry 3 \\\n  https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc\nchmod a+r /etc/apt/keyrings/docker.asc\ncat >/etc/apt/sources.list.d/docker.sources <<SOURCES\nTypes: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: noble\nComponents: stable\nArchitectures: amd64\nSigned-By: /etc/apt/keyrings/docker.asc\nSOURCES\napt-get -o Acquire::Retries=3 update\napt-get -o Acquire::Retries=3 install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin\nsystemctl stop docker docker.socket\ninstall -d -m 0755 /etc/docker /etc/systemd/system/docker.service.d\ncat >/etc/docker/daemon.json <<'DOCKER'\n{\n  \"data-root\": \"/srv/threadly-data/docker\",\n  \"log-driver\": \"json-file\",\n  \"log-opts\": {\"max-size\": \"10m\", \"max-file\": \"3\"}\n}\nDOCKER\ncat >/etc/systemd/system/docker.service.d/storage.conf <<'UNIT'\n[Unit]\nRequiresMountsFor=/srv/threadly-data\n[Service]\nTimeoutStopSec=120\nUNIT\nsystemctl daemon-reload\nsystemctl enable --now docker\n\n# Official Ubuntu EC2 images normally include the SSM agent snap. Ensure it is running.\nif ! snap list amazon-ssm-agent >/dev/null 2>&1; then\n  snap install amazon-ssm-agent --classic\nfi\nsnap start --enable amazon-ssm-agent\n\n# Empty deployment workspace and protected local credentials. No Google secrets\n# are requested or printed, no application is launched, and no model is invoked.\ninstall -d -m 0755 /opt/threadly\ninstall -d -m 0700 /srv/threadly-data/secrets\npython3 - <<'PY'\nimport base64\nimport os\nimport secrets\nfrom pathlib import Path\npath = Path('/srv/threadly-data/secrets/threadly.env')\nif not path.exists():\n    password = secrets.token_hex(24)\n    values = {\n        'APP_ENV': 'prod',\n        'INFERENCE_PROVIDER': 'bedrock',\n        'BEDROCK_REGION': '${AWS::Region}',\n        'BEDROCK_MODEL_ID': '',\n        'BEDROCK_SMALL_MODEL_ID': '',\n        'DOMAIN': '',\n        'SECRET_KEY': secrets.token_hex(32),\n        'FERNET_KEY': base64.urlsafe_b64encode(os.urandom(32)).decode(),\n        'POSTGRES_USER': 'threadly',\n        'POSTGRES_PASSWORD': password,\n        'POSTGRES_DB': 'threadly',\n        'DATABASE_URL': f'postgresql+asyncpg://threadly:{password}@postgres:5432/threadly',\n        'CHROMA_URL': 'http://chroma:8000',\n        'GOOGLE_CLIENT_ID': '',\n        'GOOGLE_CLIENT_SECRET': '',\n        'GOOGLE_REDIRECT_URI': '',\n    }\n    with path.open('x') as handle:\n        os.chmod(path, 0o600)\n        handle.write('\\n'.join(f'{k}={v}' for k, v in values.items()) + '\\n')\nPY\n# No hard-coded checkout of stale or unreviewed application code.\ndocker version\ndocker compose version\nprintf 'Host bootstrap complete. Application configuration/deployment is still pending.\\n' >/opt/threadly/BOOTSTRAP_READY\n"
            }
          }
        }
      }
    },
    "Instance": {
      "Type": "AWS::EC2::Instance",
      "Properties": {
        "LaunchTemplate": {
          "LaunchTemplateId": {
            "Ref": "LaunchTemplate"
          },
          "Version": {
            "Fn::GetAtt": [
              "LaunchTemplate",
              "LatestVersionNumber"
            ]
          }
        },
        "Tags": [
          {
            "Key": "Project",
            "Value": "Threadly"
          },
          {
            "Key": "Environment",
            "Value": "staging"
          },
          {
            "Key": "Name",
            "Value": {
              "Ref": "AWS::StackName"
            }
          }
        ]
      },
      "DependsOn": [
        "DefaultRoute",
        "RouteAssociation"
      ]
    },
    "DataAttachment": {
      "Type": "AWS::EC2::VolumeAttachment",
      "Properties": {
        "Device": "/dev/sdf",
        "InstanceId": {
          "Ref": "Instance"
        },
        "VolumeId": {
          "Ref": "DataVolume"
        }
      }
    },
    "ElasticIp": {
      "Type": "AWS::EC2::EIP",
      "Properties": {
        "Domain": "vpc",
        "Tags": [
          {
            "Key": "Project",
            "Value": "Threadly"
          },
          {
            "Key": "Environment",
            "Value": "staging"
          },
          {
            "Key": "Name",
            "Value": {
              "Ref": "AWS::StackName"
            }
          }
        ]
      },
      "DependsOn": "GatewayAttachment"
    },
    "ElasticIpAssociation": {
      "Type": "AWS::EC2::EIPAssociation",
      "Properties": {
        "AllocationId": {
          "Fn::GetAtt": [
            "ElasticIp",
            "AllocationId"
          ]
        },
        "InstanceId": {
          "Ref": "Instance"
        }
      }
    }
  },
  "Outputs": {
    "InstanceId": {
      "Value": {
        "Ref": "Instance"
      }
    },
    "PublicIp": {
      "Value": {
        "Ref": "ElasticIp"
      }
    },
    "DataVolumeId": {
      "Value": {
        "Ref": "DataVolume"
      }
    },
    "BackupBucket": {
      "Value": {
        "Ref": "BackupBucket"
      }
    },
    "Region": {
      "Value": {
        "Ref": "AWS::Region"
      }
    },
    "SessionManagerUrl": {
      "Value": {
        "Fn::Sub": "https://${AWS::Region}.console.aws.amazon.com/systems-manager/session-manager/${Instance}?region=${AWS::Region}"
      }
    }
  }
}
THREADLY_CLOUDFORMATION

printf '\nCreating BILLABLE staging resources: %s, 80 GiB gp3, one Elastic IP and a private S3 bucket.\n' "$INSTANCE_TYPE"
printf 'The server stops after %s hours per boot. EBS, the Elastic IP and stored backups still cost money while stopped.\n' "$AUTO_STOP_HOURS"
printf 'AWS credits are not a spending cap. Bedrock usage is separate.\n\n'
aws cloudformation validate-template --region "$REGION" --template-body "file://$WORKDIR/stack.json" --query Description --output text
aws cloudformation create-stack --region "$REGION" --stack-name "$STACK" \
    --template-body "file://$WORKDIR/stack.json" --parameters "file://$WORKDIR/parameters.json" \
    --capabilities CAPABILITY_IAM --enable-termination-protection --on-failure ROLLBACK \
    --tags Key=Project,Value=Threadly Key=Environment,Value=staging --output json

printf 'Waiting for CloudFormation. If this shell disconnects, check the existing stack before rerunning.\n'
if ! aws cloudformation wait stack-create-complete --region "$REGION" --stack-name "$STACK"; then
    aws cloudformation describe-stack-events --region "$REGION" --stack-name "$STACK" \
        --query 'StackEvents[?contains(ResourceStatus, `FAILED`)].[LogicalResourceId,ResourceStatusReason]' --output table || true
    die "Stack did not finish successfully within the wait period. Inspect CloudFormation; do not create a second stack."
fi
aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" --query 'Stacks[0].Outputs' --output json > "$WORKDIR/outputs.json"
python3 - "$WORKDIR/outputs.json" <<'PY'
import json, sys
for output in json.load(open(sys.argv[1])):
    print(output["OutputKey"] + ": " + output["OutputValue"])
PY
INSTANCE_ID=$(python3 - "$WORKDIR/outputs.json" <<'PY'
import json, re, sys
value = next(o["OutputValue"] for o in json.load(open(sys.argv[1])) if o["OutputKey"] == "InstanceId")
if not re.fullmatch(r"i-[0-9a-f]+", value):
    sys.exit("Invalid instance ID returned by CloudFormation.")
print(value)
PY
)
printf '\nInfrastructure created. Docker installation may still be running; the Threadly application is NOT deployed yet.\n'
printf 'Connect with the SessionManagerUrl above once the agent is online, then check:\n'
printf '  sudo cloud-init status --wait\n  sudo cat /opt/threadly/BOOTSTRAP_READY\n  sudo docker compose version\n'
printf '\nDaily controls (paste into CloudShell):\n'
printf '  aws ec2 stop-instances --region %s --instance-ids %s\n' "$REGION" "$INSTANCE_ID"
printf '  aws ec2 start-instances --region %s --instance-ids %s\n' "$REGION" "$INSTANCE_ID"
printf '\nTo extend the current work session, run ON THE SERVER:\n  sudo systemctl restart threadly-autostop.timer\n'
printf '\nNext: configure domain, Google OAuth and a supported Bedrock model, then deploy the application.\n'
printf 'Private environment file on server: /srv/threadly-data/secrets/threadly.env (root only).\n'
printf 'The S3 bucket is ready for backups, but no backup job has been installed.\n'
printf 'Keep %s/outputs.json: the data disk and backup bucket are retained if the stack is later deleted.\n' "$WORKDIR"
