#!/bin/bash

# YouTube Bot Quick Update Script
# This script updates your bot from GitHub

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Configuration
GITHUB_URL="https://raw.githubusercontent.com/MrTimonM/formatter/main/hehe.py"
BOT_USER="ytbot"
BOT_DIR="/home/$BOT_USER/youtube-bot"
SERVICE_NAME="youtube-bot"

echo -e "${BLUE}=========================================${NC}"
echo -e "${BLUE}  YouTube Bot Quick Update               ${NC}"
echo -e "${BLUE}=========================================${NC}"

# Check if running as root
if [[ $EUID -ne 0 ]]; then
    echo -e "${YELLOW}This script must be run as root. Please run: sudo $0${NC}"
    exit 1
fi

echo -e "${GREEN}[1/4]${NC} Stopping bot service..."
systemctl stop "$SERVICE_NAME.service"

echo -e "${GREEN}[2/4]${NC} Downloading latest version from GitHub..."
sudo -u "$BOT_USER" wget -O "$BOT_DIR/main.py" "$GITHUB_URL"

echo -e "${GREEN}[3/4]${NC} Starting bot service..."
systemctl start "$SERVICE_NAME.service"

echo -e "${GREEN}[4/4]${NC} Checking status..."
sleep 3

if systemctl is-active --quiet "$SERVICE_NAME.service"; then
    echo -e "${GREEN}✅ Bot updated and running successfully!${NC}"
    systemctl status "$SERVICE_NAME.service" --no-pager -l
else
    echo -e "${YELLOW}❌ Bot update failed. Checking logs...${NC}"
    journalctl -u "$SERVICE_NAME.service" --no-pager -l -n 20
fi

echo ""
echo -e "${BLUE}Update completed!${NC}"
