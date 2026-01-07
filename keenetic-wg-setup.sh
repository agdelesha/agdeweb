#!/bin/bash

# Keenetic WireGuard Setup Script
# This script will configure WireGuard on Keenetic router via CLI

echo "=== Keenetic WireGuard Configuration Script ==="
echo ""

# Variables - REPLACE WITH YOUR VALUES
WG_PRIVATE_KEY="YOUR_PRIVATE_KEY_HERE"
WG_ADDRESS="YOUR_VPN_IP_HERE"  # e.g., 10.0.0.2/32
WG_PRESHARED_KEY="YOUR_PRESHARED_KEY_HERE"
WG_PUBLIC_KEY="SERVER_PUBLIC_KEY_HERE"
WG_ENDPOINT="SERVER_IP:PORT_HERE"  # e.g., vpn.example.com:51820
WG_ALLOWED_IPS="0.0.0.0/0"  # Route all traffic through VPN initially

echo "Step 1: Creating WireGuard interface..."
echo "interface Wireguard0"
echo "  description \"WireGuard VPN\""
echo "  ip address $WG_ADDRESS"
echo "  private-key $WG_PRIVATE_KEY"
echo "  mtu 1420"
echo "  up"
echo "  exit"
echo ""

echo "Step 2: Adding WireGuard peer..."
echo "interface Wireguard0"
echo "  peer"
echo "    public-key $WG_PUBLIC_KEY"
echo "    preshared-key $WG_PRESHARED_KEY"
echo "    endpoint $WG_ENDPOINT"
echo "    allowed-ips $WG_ALLOWED_IPS"
echo "    persistent-keepalive 25"
echo "    exit"
echo "  exit"
echo ""

echo "Step 3: Configuration complete!"
echo "Next, run the routing script to configure selective routing."
