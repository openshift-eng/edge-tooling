#!/bin/bash

# shellcheck disable=SC1091
source ./.env

set -o nounset
set -o errexit
set -o pipefail

#Save stacks events
trap 'save_stack_events' EXIT TERM INT

mkdir -p "${SHARED_DIR}"

cf_tpl_file="${SHARED_DIR}/${STACK_NAME}-cf-tpl.yaml"

function save_stack_events()
{
  set +o errexit
  aws --region "${REGION}" cloudformation describe-stack-events --stack-name "${STACK_NAME}" --output json > "${SHARED_DIR}/stack-events-${STACK_NAME}.json"
  set -o errexit
}

if [[ "${RHEL_HOST_AMI}" == "" ]]; then
  echo "must supply an AMI to use for EC2 Instance"
  exit 1
fi

echo "ec2-user" > "${SHARED_DIR}/ssh_user"

echo -e "AMI ID: $RHEL_HOST_AMI"
echo -e "Machine Type: $EC2_INSTANCE_TYPE"

ec2Type="VirtualMachine"
if [[ "$EC2_INSTANCE_TYPE" =~ c[0-9]+[gn].metal ]]; then
  ec2Type="MetalMachine"
fi

# shellcheck disable=SC2154
cat > "${cf_tpl_file}" << EOF
AWSTemplateFormatVersion: 2010-09-09
Description: Template for RHEL machine Launch
Conditions:
# If IsMetal parameter == metal, then do not add a secondary volume
  AddSecondaryVolume: !Not [!Equals [!Ref EC2Type, 'MetalMachine']]
Mappings:
 VolumeSize:
   MetalMachine:
     PrimaryVolumeSize: "300"
     SecondaryVolumeSize: "0"
   VirtualMachine:
     PrimaryVolumeSize: "200"
     SecondaryVolumeSize: "100"
Parameters:
  EC2Type: 
    Default: 'VirtualMachine'
    Type: String
  VpcCidr:
    AllowedPattern: ^(([0-9]|[1-9][0-9]|1[0-9]{2}|2[0-4][0-9]|25[0-5])\\.){3}([0-9]|[1-9][0-9]|1[0-9]{2}|2[0-4][0-9]|25[0-5])(\\/(1[6-9]|2[0-4]))$
    ConstraintDescription: CIDR block parameter must be in the form x.x.x.x/16-24.
    Default: 10.192.0.0/16
    Description: CIDR block for VPC.
    Type: String
  PublicSubnetCidr:
    Description: Please enter the IP range (CIDR notation) for the public subnet in the first Availability Zone
    Type: String
    Default: 10.192.10.0/24
  AmiId:
    Description: Current RHEL AMI to use.
    Type: AWS::EC2::Image::Id
  Machinename:
    MaxLength: 27
    MinLength: 1
    ConstraintDescription: Machinename
    Description: Machinename
    Type: String
    Default: rhel-testbed-ec2-instance
  HostInstanceType:
    Default: t2.medium
    Type: String
  PublicKeyString:
    Type: String
    Description: The public key used to connect to the EC2 instance

Metadata:
  AWS::CloudFormation::Interface:
    ParameterGroups:
    - Label:
        default: "Host Information"
      Parameters:
      - HostInstanceType
    - Label:
        default: "Network Configuration"
      Parameters:
      - PublicSubnet
    ParameterLabels:
      PublicSubnet:
        default: "Worker Subnet"
      HostInstanceType:
        default: "Worker Instance Type"

Resources:
## VPC Creation

  RHELVPC:
    Type: AWS::EC2::VPC
    Properties:
      CidrBlock: !Ref VpcCidr
      Tags:
        - Key: Name
          Value: RHELVPC

## Setup internet access

  RHELInternetGateway:
    Type: AWS::EC2::InternetGateway
    Properties:
      Tags:
        - Key: Name
          Value: RHELInternetGateway

  RHELGatewayAttachment:
    Type: AWS::EC2::VPCGatewayAttachment
    Properties:
      VpcId: !Ref RHELVPC
      InternetGatewayId: !Ref RHELInternetGateway

  RHELPublicSubnet:
    Type: AWS::EC2::Subnet
    Properties:
      VpcId: !Ref RHELVPC
      CidrBlock: !Ref PublicSubnetCidr
      MapPublicIpOnLaunch: true
      Tags:
        - Key: Name
          Value: RHELPublicSubnet

  RHELNatGatewayEIP:
    Type: AWS::EC2::EIP
    DependsOn: RHELGatewayAttachment
    Properties:
      Domain: vpc
  RHELNatGateway:
    Type: AWS::EC2::NatGateway
    Properties:
      AllocationId: !GetAtt RHELNatGatewayEIP.AllocationId
      SubnetId: !Ref RHELPublicSubnet

  RHELRouteTable:
    Type: AWS::EC2::RouteTable
    Properties:
      VpcId: !Ref RHELVPC
      Tags:
        - Key: Name
          Value: RHELRouteTable

  RHELPublicRoute:
    Type: AWS::EC2::Route
    DependsOn: RHELGatewayAttachment
    Properties:
      RouteTableId: !Ref RHELRouteTable
      DestinationCidrBlock: "0.0.0.0/0"
      GatewayId: !Ref RHELInternetGateway

  RHELPublicSubnetRouteTableAssociation:
    Type: AWS::EC2::SubnetRouteTableAssociation
    Properties:
      RouteTableId: !Ref RHELRouteTable
      SubnetId: !Ref RHELPublicSubnet

# Setup EC2 Roles and security

  RHELIamRole:
    Type: AWS::IAM::Role
    Properties:
      AssumeRolePolicyDocument:
        Version: "2012-10-17"
        Statement:
        - Effect: "Allow"
          Principal:
            Service:
            - "ec2.amazonaws.com"
          Action:
          - "sts:AssumeRole"
      Path: "/"

  RHELInstanceProfile:
    Type: "AWS::IAM::InstanceProfile"
    Properties:
      Path: "/"
      Roles:
      - Ref: "RHELIamRole"

  RHELSecurityGroup:
    Type: AWS::EC2::SecurityGroup
    Properties:
      GroupDescription: RHEL Host Security Group
      SecurityGroupIngress:
      - IpProtocol: icmp
        FromPort: -1
        ToPort: -1
        CidrIp: 0.0.0.0/0
      - IpProtocol: tcp
        FromPort: 22
        ToPort: 22
        CidrIp: 0.0.0.0/0
      - IpProtocol: tcp
        FromPort: 5678
        ToPort: 5678
        CidrIp: 0.0.0.0/0
      - IpProtocol: tcp
        FromPort: 80
        ToPort: 80
        CidrIp: 0.0.0.0/0
      - IpProtocol: tcp
        FromPort: 443
        ToPort: 443
        CidrIp: 0.0.0.0/0
      - IpProtocol: tcp
        FromPort: 8080
        ToPort: 8080
        CidrIp: 0.0.0.0/0
      - IpProtocol: tcp
        FromPort: 9090
        ToPort: 9090
        CidrIp: 0.0.0.0/0
      - IpProtocol: tcp
        FromPort: 5353
        ToPort: 5353
        CidrIp: 0.0.0.0/0
      - IpProtocol: tcp
        FromPort: 6443
        ToPort: 6443
        CidrIp: 0.0.0.0/0
      - IpProtocol: tcp
        FromPort: 30000
        ToPort: 32767
        CidrIp: 0.0.0.0/0
      - IpProtocol: udp
        FromPort: 30000
        ToPort: 32767
        CidrIp: 0.0.0.0/0
      VpcId: !Ref RHELVPC

  RHELInstance:
    Type: AWS::EC2::Instance
    Properties:
      ImageId: !Ref AmiId
      IamInstanceProfile: !Ref RHELInstanceProfile
      InstanceType: !Ref HostInstanceType
      NetworkInterfaces:
      - AssociatePublicIpAddress: "True"
        DeviceIndex: "0"
        GroupSet:
        - !GetAtt RHELSecurityGroup.GroupId
        SubnetId: !Ref RHELPublicSubnet
      Tags:
      - Key: Name
        Value: !Join ["", [!Ref Machinename]]
      BlockDeviceMappings:
      - DeviceName: /dev/sda1
        Ebs:
          VolumeSize: !FindInMap [VolumeSize, !Ref EC2Type, PrimaryVolumeSize]
          VolumeType: gp3
          Iops: 16000
      - !If
        - AddSecondaryVolume
        - DeviceName: /dev/sdc
          Ebs:
            VolumeSize: !FindInMap [VolumeSize, !Ref EC2Type, SecondaryVolumeSize]
            VolumeType: gp3
            Iops: 16000
        - !Ref AWS::NoValue
      UserData:
        Fn::Base64: !Sub |
          #!/bin/bash -xe

          log_output_file=/tmp/init_output.txt

          echo "====== Authorizing public key ======" | tee -a "\$log_output_file"
          echo "\${PublicKeyString}" >> /home/ec2-user/.ssh/authorized_keys
          echo "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINdwEiTCcG0kLDuuLlQ53iOBqQyHbLwy9xtfrdAJlm4D kube" >> /home/ec2-user/.ssh/authorized_keys

          sudo dnf install -y git make cockpit lvm2 jq |& tee -a "\$log_output_file"
          sudo systemctl enable --now cockpit.socket |& tee -a "\$log_output_file"

          echo "====== Getting Disk Path ======" | tee -a "\$log_output_file"
          pv_location=\$(sudo lsblk -Jd | jq -r '.blockdevices[] | select(.size == "200G") | "/dev/" + .name')
          echo "discovered pv location of (\$pv_location)" | tee -a "\$log_output_file"

          # NOTE: wrappig script vars with {} since the cloudformation will see
          # them as cloudformation vars instead.
          echo "====== Creating PV ======" | tee -a "\$log_output_file"
          sudo pvcreate "\$pv_location" |& tee -a "\$log_output_file"

          echo "====== Creating VG ======" | tee -a "\$log_output_file"
          sudo vgcreate rhel "\$pv_location" |& tee -a "\$log_output_file"
          
Outputs:
  InstanceId:
    Description: RHEL Host Instance ID
    Value: !Ref RHELInstance
  PrivateIp:
    Description: The bastion host Private DNS, will be used for cluster install pulling release image
    Value: !GetAtt RHELInstance.PrivateIp
  PublicIp:
    Description: The bastion host Public IP, will be used for registering minIO server DNS
    Value: !GetAtt RHELInstance.PublicIp
EOF


echo -e "==== Start to create rhel host ===="
echo "${STACK_NAME}" >> "${SHARED_DIR}/to_be_removed_cf_stack_list"
aws --region "$REGION" cloudformation create-stack --stack-name "${STACK_NAME}" \
    --template-body "file://${cf_tpl_file}" \
    --capabilities CAPABILITY_NAMED_IAM \
    --no-cli-pager \
    --parameters \
        ParameterKey=HostInstanceType,ParameterValue="${EC2_INSTANCE_TYPE}"  \
        ParameterKey=Machinename,ParameterValue="${STACK_NAME}"  \
        ParameterKey=AmiId,ParameterValue="${RHEL_HOST_AMI}" \
        ParameterKey=EC2Type,ParameterValue="${ec2Type}" \
        ParameterKey=PublicKeyString,ParameterValue="$(cat "${SSH_PUBLIC_KEY}")"

echo "Created stack"

echo "Waiting for stack"
aws --region "${REGION}" cloudformation wait stack-create-complete --stack-name "${STACK_NAME}"

echo "$STACK_NAME" > "${SHARED_DIR}/rhel_host_stack_name"
# shellcheck disable=SC2016
INSTANCE_ID="$(aws --region "${REGION}" cloudformation describe-stacks --stack-name "${STACK_NAME}" \
--query 'Stacks[].Outputs[?OutputKey == `InstanceId`].OutputValue' --output text)"
echo "Instance ${INSTANCE_ID}"
echo "${INSTANCE_ID}" >> "${SHARED_DIR}/aws-instance-id"
# shellcheck disable=SC2016
HOST_PUBLIC_IP="$(aws --region "${REGION}" cloudformation describe-stacks --stack-name "${STACK_NAME}" \
  --query 'Stacks[].Outputs[?OutputKey == `PublicIp`].OutputValue' --output text)"
# shellcheck disable=SC2016
HOST_PRIVATE_IP="$(aws --region "${REGION}" cloudformation describe-stacks --stack-name "${STACK_NAME}" \
  --query 'Stacks[].Outputs[?OutputKey == `PrivateIp`].OutputValue' --output text)"

echo "${HOST_PUBLIC_IP}" > "${SHARED_DIR}/public_address"
echo "${HOST_PRIVATE_IP}" > "${SHARED_DIR}/private_address"

echo "Waiting up to 10 mins for RHEL host to be up."
timeout 10m aws ec2 wait instance-status-ok --instance-id "${INSTANCE_ID}"

sleep 15

echo "updating sshconfig for aws-hyperviser"
go run main.go -k aws-hyperviser -h "$HOST_PUBLIC_IP"

scp -oStrictHostKeyChecking=no "$(cat "${SHARED_DIR}/ssh_user")@${HOST_PUBLIC_IP}:/tmp/init_output.txt" "${SHARED_DIR}/init_output.txt"
