#!/bin/bash

# YouTube Bot One-Click Deployment Script
# This script will download, install, and configure your YouTube bot for persistent operation

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
GITHUB_URL="https://raw.githubusercontent.com/MrTimonM/formatter/main/hehe.py"
BOT_USER="ytbot"
BOT_DIR="/home/$BOT_USER/youtube-bot"
SERVICE_NAME="youtube-bot"

# Function to print colored output
print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_step() {
    echo -e "${BLUE}[STEP]${NC} $1"
}

# Function to check if running as root
check_root() {
    if [[ $EUID -eq 0 ]]; then
        print_status "Running as root. Good!"
    else
        print_error "This script must be run as root. Please run: sudo $0"
        exit 1
    fi
}

# Function to update system
update_system() {
    print_step "Updating system packages..."
    apt update && apt upgrade -y
    print_status "System updated successfully"
}

# Function to install dependencies
install_dependencies() {
    print_step "Installing required packages..."
    apt install -y python3 python3-pip python3-venv git curl wget unzip ffmpeg
    
    # Verify installations
    print_status "Verifying installations..."
    python3 --version
    pip3 --version
    git --version
    ffmpeg -version | head -1
    print_status "All dependencies installed successfully"
}

# Function to create bot user
create_bot_user() {
    print_step "Creating bot user and directory..."
    
    # Create user if doesn't exist
    if ! id "$BOT_USER" &>/dev/null; then
        useradd -m -s /bin/bash "$BOT_USER"
        print_status "User $BOT_USER created"
    else
        print_warning "User $BOT_USER already exists"
    fi
    
    # Create bot directory
    sudo -u "$BOT_USER" mkdir -p "$BOT_DIR"
    print_status "Bot directory created at $BOT_DIR"
}

# Function to download bot files
download_bot() {
    print_step "Downloading bot from GitHub..."
    
    # Download main.py from GitHub
    sudo -u "$BOT_USER" wget -O "$BOT_DIR/main.py" "$GITHUB_URL"
    
    # Create requirements.txt
    sudo -u "$BOT_USER" cat > "$BOT_DIR/requirements.txt" << 'EOF'
python-telegram-bot==20.4
yt-dlp==2023.7.6
asyncio
aiofiles
python-dotenv==1.0.0
EOF
    
    print_status "Bot files downloaded successfully"
}

# Function to setup Python environment
setup_python_env() {
    print_step "Setting up Python virtual environment..."
    
    cd "$BOT_DIR"
    
    # Create virtual environment
    sudo -u "$BOT_USER" python3 -m venv venv
    
    # Install dependencies
    sudo -u "$BOT_USER" bash -c "source venv/bin/activate && pip install --upgrade pip && pip install -r requirements.txt"
    
    print_status "Python environment setup completed"
}

# Function to configure environment variables
configure_env() {
    print_step "Configuring environment variables..."
    
    # Prompt for bot token
    echo -e "${YELLOW}Please enter your Telegram Bot Token:${NC}"
    read -p "Bot Token: " BOT_TOKEN
    
    echo -e "${YELLOW}Please enter your Telegram User ID (for admin access):${NC}"
    echo -e "${YELLOW}You can get this from @userinfobot on Telegram${NC}"
    read -p "User ID: " ADMIN_ID
    
    # Create .env file
    sudo -u "$BOT_USER" cat > "$BOT_DIR/.env" << EOF
BOT_TOKEN=$BOT_TOKEN
ADMIN_USER_IDS=$ADMIN_ID
MAX_DURATION_MINUTES=120
MAX_FILE_SIZE_MB=2048
EOF
    
    print_status "Environment variables configured"
}

# Function to create systemd service
create_service() {
    print_step "Creating systemd service for persistent operation..."
    
    cat > "/etc/systemd/system/$SERVICE_NAME.service" << EOF
[Unit]
Description=YouTube Downloader Telegram Bot
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=$BOT_USER
WorkingDirectory=$BOT_DIR
Environment=PATH=$BOT_DIR/venv/bin
ExecStart=$BOT_DIR/venv/bin/python main.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

# Security settings
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$BOT_DIR

[Install]
WantedBy=multi-user.target
EOF
    
    # Set proper permissions
    chown -R "$BOT_USER":"$BOT_USER" "$BOT_DIR"
    chmod +x "$BOT_DIR/main.py"
    
    # Reload systemd and enable service
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME.service"
    
    print_status "Systemd service created and enabled"
}

# Function to test bot
test_bot() {
    print_step "Testing bot functionality..."
    
    print_status "Starting bot service..."
    systemctl start "$SERVICE_NAME.service"
    
    sleep 5
    
    # Check service status
    if systemctl is-active --quiet "$SERVICE_NAME.service"; then
        print_status "✅ Bot is running successfully!"
        systemctl status "$SERVICE_NAME.service" --no-pager -l
    else
        print_error "❌ Bot failed to start. Checking logs..."
        journalctl -u "$SERVICE_NAME.service" --no-pager -l
        exit 1
    fi
}

# Function to show management commands
show_management() {
    print_step "Bot deployment completed successfully! 🎉"
    
    echo -e "${GREEN}===========================================${NC}"
    echo -e "${GREEN}    YOUTUBE BOT DEPLOYMENT COMPLETE!     ${NC}"
    echo -e "${GREEN}===========================================${NC}"
    echo ""
    echo -e "${BLUE}Bot Management Commands:${NC}"
    echo -e "  Start bot:     ${YELLOW}sudo systemctl start $SERVICE_NAME${NC}"
    echo -e "  Stop bot:      ${YELLOW}sudo systemctl stop $SERVICE_NAME${NC}"
    echo -e "  Restart bot:   ${YELLOW}sudo systemctl restart $SERVICE_NAME${NC}"
    echo -e "  Check status:  ${YELLOW}sudo systemctl status $SERVICE_NAME${NC}"
    echo -e "  View logs:     ${YELLOW}sudo journalctl -u $SERVICE_NAME -f${NC}"
    echo ""
    echo -e "${BLUE}Bot Files Location:${NC}"
    echo -e "  Directory:     ${YELLOW}$BOT_DIR${NC}"
    echo -e "  Main script:   ${YELLOW}$BOT_DIR/main.py${NC}"
    echo -e "  Config file:   ${YELLOW}$BOT_DIR/.env${NC}"
    echo ""
    echo -e "${BLUE}Update Bot:${NC}"
    echo -e "  1. ${YELLOW}sudo systemctl stop $SERVICE_NAME${NC}"
    echo -e "  2. ${YELLOW}sudo -u $BOT_USER wget -O $BOT_DIR/main.py $GITHUB_URL${NC}"
    echo -e "  3. ${YELLOW}sudo systemctl start $SERVICE_NAME${NC}"
    echo ""
    echo -e "${GREEN}Your bot is now running 24/7 and will auto-restart on system reboot!${NC}"
    echo -e "${GREEN}Test it by sending /start to your bot on Telegram!${NC}"
}

# Main execution
main() {
    clear
    echo -e "${GREEN}=========================================${NC}"
    echo -e "${GREEN}  YouTube Bot One-Click Deployment      ${NC}"
    echo -e "${GREEN}=========================================${NC}"
    echo ""
    
    check_root
    update_system
    install_dependencies
    create_bot_user
    download_bot
    setup_python_env
    configure_env
    create_service
    test_bot
    show_management
}

# Run main function
main "$@"
