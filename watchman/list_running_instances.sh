#!/bin/bash

# Script to find all running EC2 instances across all AWS regions
# Requires AWS CLI to be installed and configured

set -e

echo "Finding all running EC2 instances across all AWS regions..."
echo ""

# Get all available regions
regions=$(aws ec2 describe-regions --query 'Regions[].RegionName' --output text)

total_instances=0
regions_with_instances=0

# Iterate through each region
for region in $regions; do
    # Get running instances in this region
    instances=$(aws ec2 describe-instances \
        --region "$region" \
        --filters "Name=instance-state-name,Values=running" \
        --query "Reservations[].Instances[].[InstanceId,InstanceType,LaunchTime,Tags[?Key==\`Name\`].Value|[0]]" \
        --output text 2>/dev/null)
    
    if [ -n "$instances" ]; then
        instance_count=$(echo "$instances" | wc -l | tr -d ' ')
        total_instances=$((total_instances + instance_count))
        regions_with_instances=$((regions_with_instances + 1))
        
        echo "=== Region: $region ($instance_count instance(s)) ==="
        echo "$instances" | while IFS=$'\t' read -r instance_id instance_type launch_time name; do
            # Format launch time for better readability
            launch_time_formatted=$(date -d "$launch_time" +"%Y-%m-%d %H:%M:%S" 2>/dev/null || echo "$launch_time")
            name_tag="${name:-<no-name>}"
            printf '  %-20s %-15s %-19s %s\n' "$instance_id" "$instance_type" "$launch_time_formatted" "$name_tag"
        done
        echo ""
    fi
done

echo "=========================================="
echo "Summary:"
echo "  Total running instances: $total_instances"
echo "  Regions with instances: $regions_with_instances"
echo "  Total regions checked: $(echo "$regions" | wc -w | tr -d ' ')"
echo "=========================================="
