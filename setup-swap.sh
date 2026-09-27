#!/bin/bash
# Tạo swap 2GB cho VPS 1GB RAM
echo "=== RAM hiện tại ==="
free -h

if [ ! -f /swapfile ]; then
  echo "Tạo swapfile 2GB..."
  sudo fallocate -l 2G /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count=2048
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
  echo "Swap đã tạo"
else
  echo "Swap đã tồn tại"
  sudo swapon /swapfile || true
fi

echo "=== Sau khi tạo swap ==="
free -h
swapon --show
