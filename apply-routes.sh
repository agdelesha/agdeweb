#!/bin/bash

# Script to apply routes to Keenetic router via SSH
# Usage: ./apply-routes.sh <router_ip> <username>

ROUTER_IP=${1:-""}
USERNAME=${2:-"admin"}

if [ -z "$ROUTER_IP" ]; then
    echo "Usage: $0 <router_ip> [username]"
    echo "Example: $0 192.168.1.1 admin"
    exit 1
fi

echo "Connecting to router at $ROUTER_IP as $USERNAME"
echo "This will apply routes for YouTube, Instagram, and OpenAI through WireGuard"
echo ""

# Create the commands file
cat > /tmp/keenetic_routes.txt << 'EOF'
configure

# YouTube routes (Google/YouTube IP ranges)
ip route 64.233.161.0/24 Wireguard0
ip route 64.233.162.0/24 Wireguard0
ip route 64.233.163.0/24 Wireguard0
ip route 64.233.164.0/24 Wireguard0
ip route 64.233.165.0/24 Wireguard0
ip route 74.125.0.0/16 Wireguard0
ip route 108.177.14.0/24 Wireguard0
ip route 142.250.0.0/15 Wireguard0
ip route 172.217.0.0/16 Wireguard0
ip route 173.194.0.0/16 Wireguard0
ip route 216.58.192.0/19 Wireguard0
ip route 216.239.32.0/19 Wireguard0
ip route 209.85.128.0/17 Wireguard0

# Instagram/Facebook/Meta routes
ip route 31.13.24.0/21 Wireguard0
ip route 31.13.64.0/18 Wireguard0
ip route 66.220.144.0/20 Wireguard0
ip route 69.63.176.0/20 Wireguard0
ip route 157.240.0.0/16 Wireguard0
ip route 185.60.216.0/22 Wireguard0
ip route 204.15.20.0/22 Wireguard0

# OpenAI routes (Cloudflare ranges used by OpenAI)
ip route 104.18.0.0/20 Wireguard0
ip route 172.64.0.0/13 Wireguard0
ip route 104.16.0.0/13 Wireguard0
ip route 104.24.0.0/14 Wireguard0

system configuration save
exit
EOF

echo "Applying routes to router..."
ssh -o StrictHostKeyChecking=no $USERNAME@$ROUTER_IP < /tmp/keenetic_routes.txt

if [ $? -eq 0 ]; then
    echo ""
    echo "✓ Routes applied successfully!"
    echo ""
    echo "To verify, run:"
    echo "  ssh $USERNAME@$ROUTER_IP 'show ip route'"
else
    echo ""
    echo "✗ Failed to apply routes"
    echo "Please check:"
    echo "  1. SSH is enabled on the router"
    echo "  2. Router IP is correct: $ROUTER_IP"
    echo "  3. Username is correct: $USERNAME"
    echo "  4. WireGuard interface 'Wireguard0' exists"
fi

rm -f /tmp/keenetic_routes.txt
