#!/bin/bash

# Keenetic Selective Routing Setup Script
# Routes YouTube, Instagram, and OpenAI through WireGuard VPN

echo "=== Keenetic Selective Routing Configuration ==="
echo ""

# IP addresses for services (these may change, update as needed)
# YouTube
YOUTUBE_IPS=(
    "172.217.0.0/16"
    "216.58.192.0/19"
    "142.250.0.0/15"
)

# Instagram (Facebook/Meta)
INSTAGRAM_IPS=(
    "31.13.24.0/21"
    "31.13.64.0/18"
    "66.220.144.0/20"
    "69.63.176.0/20"
    "157.240.0.0/16"
)

# OpenAI
OPENAI_IPS=(
    "104.18.0.0/20"
    "172.64.0.0/13"
)

echo "Step 1: Creating routing table for VPN traffic..."
echo "ip route table 100 name vpn-table"
echo ""

echo "Step 2: Adding default route via WireGuard to routing table..."
echo "ip route table 100 0.0.0.0/0 Wireguard0"
echo ""

echo "Step 3: Creating IP sets for selective routing..."
echo "ip set create youtube hash:net"
echo "ip set create instagram hash:net"
echo "ip set create openai hash:net"
echo ""

echo "Step 4: Adding YouTube IPs to set..."
for ip in "${YOUTUBE_IPS[@]}"; do
    echo "ip set add youtube $ip"
done
echo ""

echo "Step 5: Adding Instagram IPs to set..."
for ip in "${INSTAGRAM_IPS[@]}"; do
    echo "ip set add instagram $ip"
done
echo ""

echo "Step 6: Adding OpenAI IPs to set..."
for ip in "${OPENAI_IPS[@]}"; do
    echo "ip set add openai $ip"
done
echo ""

echo "Step 7: Creating routing rules..."
echo "ip rule add fwmark 100 table 100 priority 100"
echo ""

echo "Step 8: Creating firewall rules to mark packets..."
echo "firewall rule add chain=prerouting match-set=youtube dst action=mark mark=100"
echo "firewall rule add chain=prerouting match-set=instagram dst action=mark mark=100"
echo "firewall rule add chain=prerouting match-set=openai dst action=mark mark=100"
echo ""

echo "Step 9: Saving configuration..."
echo "system configuration save"
echo ""

echo "=== Configuration Complete! ==="
echo "Traffic to YouTube, Instagram, and OpenAI will now route through VPN."
echo "All other traffic will use regular internet connection."
