#!/bin/bash
# Setup Pertek Checker - jalankan sekali saja untuk install dependencies

cd "$(dirname "$0")"

echo "================================="
echo "  Setup Pertek Checker"
echo "================================="
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python3 belum terinstall!"
    echo "Download di: https://www.python.org/downloads/"
    echo ""
    read -p "Tekan Enter untuk keluar..."
    exit 1
fi

echo "Python3 ditemukan: $(python3 --version)"
echo ""

# Install dependencies
echo "Menginstall dependencies..."
pip3 install -r requirements.txt

echo ""
echo "================================="
echo "  Setup selesai!"
echo "  Sekarang double-click 'jalankan.command' untuk membuka aplikasi"
echo "================================="
echo ""
read -p "Tekan Enter untuk keluar..."
