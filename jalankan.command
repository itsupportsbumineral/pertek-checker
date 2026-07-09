#!/bin/bash
# Jalankan Pertek Checker

cd "$(dirname "$0")"

echo "================================="
echo "  Membuka Pertek Checker..."
echo "================================="
echo ""
echo "Aplikasi akan terbuka di browser."
echo "Untuk menutup, tekan Ctrl+C di terminal ini."
echo ""

python3 -m streamlit run app.py --server.address localhost --server.port 8501
