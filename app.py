import streamlit as st
import pdfplumber
import json
import io
import os
import gc
import time
import requests
import gspread
import pandas as pd
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials

# ============================================================
# CONFIG
# ============================================================
st.set_page_config(
    page_title="Pertek Checker",
    page_icon="📋",
    layout="wide",
)

st.markdown("""
<style>
    /* General */
    .block-container { max-width: 1100px; }

    /* Header card */
    .header-card {
        background: linear-gradient(135deg, #1e3a5f 0%, #2d5a8e 100%);
        color: white; padding: 1.5rem 2rem; border-radius: 12px;
        margin-bottom: 1.5rem;
    }
    .header-card h1 { color: white !important; margin: 0 0 0.3rem 0; font-size: 1.8rem !important; }
    .header-card p { color: #b8d4f0; margin: 0; font-size: 0.95rem; }

    /* Info bar */
    .info-bar {
        background: #f0f7ff; border: 1px solid #c8ddf5; border-radius: 8px;
        padding: 12px 16px; margin: 0.5rem 0 1rem 0; font-size: 0.9rem;
    }
    .info-bar strong { color: #1e3a5f; }

    /* Tables */
    .summary-table { width: 100%; border-collapse: collapse; margin: 1rem 0; border-radius: 8px; overflow: hidden; }
    .summary-table th, .summary-table td {
        border: 1px solid #e2e8f0; padding: 12px 16px; text-align: left;
    }
    .summary-table th { background: #f8fafc; font-weight: 600; color: #334155; }
    .summary-table tr:hover { background: #f8fafc; }

    .detail-table { width: 100%; border-collapse: collapse; margin: 0.5rem 0; }
    .detail-table th, .detail-table td {
        border: 1px solid #e2e8f0; padding: 8px 12px; text-align: left; font-size: 0.88rem;
    }
    .detail-table th { background: #f1f5f9; font-weight: 600; color: #475569; }

    /* Status badges */
    .badge-sesuai {
        background: #dcfce7; color: #166534; padding: 2px 10px;
        border-radius: 12px; font-size: 0.82rem; font-weight: 500;
    }
    .badge-tidak {
        background: #fee2e2; color: #991b1b; padding: 2px 10px;
        border-radius: 12px; font-size: 0.82rem; font-weight: 500;
    }

    /* Section headers */
    .section-header {
        background: #f8fafc; border-left: 4px solid #2d5a8e;
        padding: 8px 14px; margin: 1.2rem 0 0.8rem 0; border-radius: 0 6px 6px 0;
        font-weight: 600; color: #1e3a5f;
    }

    /* Conclusion card */
    .conclusion-card {
        border-radius: 10px; padding: 1.2rem 1.5rem; margin: 1rem 0;
    }
    .conclusion-dapat {
        background: #f0fdf4; border: 2px solid #86efac;
    }
    .conclusion-tidak {
        background: #fef2f2; border: 2px solid #fca5a5;
    }

    /* Password page */
    .login-container {
        max-width: 400px; margin: 5rem auto; text-align: center;
    }

    /* File count */
    .file-count {
        background: #ecfdf5; color: #065f46; padding: 8px 16px;
        border-radius: 8px; font-weight: 500; font-size: 0.9rem;
    }

    /* Page info */
    .page-info { color: #64748b; font-size: 0.82rem; }
</style>
""", unsafe_allow_html=True)


# ============================================================
# PASSWORD PROTECTION
# ============================================================
def check_password():
    """Return True if user entered correct password."""
    if st.session_state.get("authenticated"):
        return True

    try:
        correct_password = st.secrets["APP_PASSWORD"]
    except Exception:
        return True

    st.markdown('<div class="login-container">', unsafe_allow_html=True)
    st.markdown('<div class="header-card"><h1>Pertek Checker</h1><p>Verifikasi dokumen PI & Pertek</p></div>', unsafe_allow_html=True)
    password = st.text_input("Password", type="password", key="password_input", placeholder="Masukkan password")
    if st.button("Masuk", type="primary", use_container_width=True):
        if password == correct_password:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Password salah.")
    st.markdown('</div>', unsafe_allow_html=True)
    return False


# ============================================================
# API KEY MANAGEMENT
# ============================================================
def load_api_key():
    try:
        return st.secrets["GEMINI_API_KEY"]
    except Exception:
        pass
    return st.session_state.get("saved_api_key", "")


# ============================================================
# GOOGLE SHEETS
# ============================================================
SHEET_HEADERS = [
    "tanggal_analisis", "nomor_pi", "tanggal_pi", "nomor_pertek",
    "tanggal_pertek", "jenis_api", "nama_perusahaan", "hs_codes",
    "negara_muat", "pelabuhan_tujuan", "total_items", "total_sesuai",
    "total_tidak_sesuai", "status", "catatan",
]


@st.cache_resource(ttl=300)
def _get_gsheet_client():
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = Credentials.from_service_account_info(
            creds_dict,
            scopes=[
                "https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/drive",
            ],
        )
        return gspread.authorize(creds)
    except Exception:
        return None


def _get_sheet():
    client = _get_gsheet_client()
    if not client:
        return None
    try:
        url = st.secrets["GSHEET_URL"]
        sheet = client.open_by_url(url).sheet1
        # Initialize headers if sheet is empty
        if not sheet.row_values(1):
            sheet.append_row(SHEET_HEADERS, value_input_option="RAW")
        return sheet
    except Exception:
        return None


def save_to_sheets(result):
    sheet = _get_sheet()
    if not sheet:
        return False
    try:
        info = result.get("info", {})
        id_items = result.get("identitas_perusahaan", {}).get("items", [])
        nama = next((i.get("pi", "") for i in id_items if i.get("aspek") == "Nama"), "-")
        spec = result.get("spesifikasi_barang", {})
        rekap = result.get("rekap_data", {})
        kesimpulan = result.get("kesimpulan", {})

        row = [
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            info.get("nomor_pi", ""),
            info.get("tanggal_pi", ""),
            info.get("nomor_pertek", ""),
            info.get("tanggal_pertek", ""),
            info.get("jenis_api", ""),
            nama,
            ", ".join(rekap.get("daftar_hs", [])),
            ", ".join(rekap.get("negara_muat", [])),
            ", ".join(rekap.get("pelabuhan_tujuan", [])),
            spec.get("total_items", 0),
            spec.get("total_sesuai", 0),
            spec.get("total_tidak_sesuai", 0),
            kesimpulan.get("status", ""),
            kesimpulan.get("catatan", ""),
        ]
        sheet.append_row(row, value_input_option="USER_ENTERED")
        return True
    except Exception:
        return False


def load_from_sheets():
    sheet = _get_sheet()
    if not sheet:
        return None
    try:
        data = sheet.get_all_records()
        if not data:
            return None
        return pd.DataFrame(data)
    except Exception:
        return None


# ============================================================
# PDF EXTRACTION
# ============================================================
def extract_pdf_text(uploaded_file, max_chars=150000, max_pages=500):
    """Extract text from uploaded PDF with memory and page limits."""
    parts = []
    total_pages = 0
    char_count = 0
    try:
        file_bytes = uploaded_file.read()
        buf = io.BytesIO(file_bytes)
        del file_bytes
        gc.collect()

        with pdfplumber.open(buf) as pdf:
            total_pages = len(pdf.pages)
            pages_to_read = min(total_pages, max_pages)

            for i in range(pages_to_read):
                if char_count >= max_chars:
                    parts.append(f"\n[... sisa {total_pages - i} halaman dilewati (batas tercapai) ...]\n")
                    break

                page = pdf.pages[i]
                page_text = page.extract_text()
                if page_text:
                    chunk = f"--- Halaman {i+1}/{total_pages} ---\n{page_text}\n\n"
                    parts.append(chunk)
                    char_count += len(chunk)
                else:
                    # Hanya extract tabel jika tidak ada teks (hindari duplikat)
                    tables = page.extract_tables()
                    if tables:
                        parts.append(f"--- Halaman {i+1}/{total_pages} ---\n")
                        for table in tables:
                            for row in table:
                                if row:
                                    cleaned = [str(cell) if cell else "" for cell in row]
                                    line = " | ".join(cleaned) + "\n"
                                    parts.append(line)
                                    char_count += len(line)
                        parts.append("\n")

                page.flush_cache()
                del page

            if pages_to_read < total_pages and char_count < max_chars:
                parts.append(f"\n[... hanya {pages_to_read} dari {total_pages} halaman yang dibaca ...]\n")

        buf.close()
        del buf
        uploaded_file.seek(0)
        gc.collect()
    except Exception as e:
        parts = [f"[Error membaca PDF: {e}]"]
    return "".join(parts), total_pages


# ============================================================
# ANALYSIS PROMPT
# ============================================================
SYSTEM_PROMPT = """Anda adalah analis verifikasi dokumen impor. Tugas Anda adalah mencocokkan dan menganalisis dokumen PI (Persetujuan Impor) dan Pertek (Persetujuan Teknis) dari Kementerian Perindustrian.

ATURAN PENTING:
- Output HANYA JSON valid, tanpa markdown code block.
- JANGAN narasi panjang, cukup kalimat singkat.
- Saat membandingkan spesifikasi/uraian barang: jika MAKNA/ISI SAMA tapi beda format penulisan, urutan kata, singkatan, atau bahasa → ANGGAP SESUAI.
- Hanya anggap Tidak Sesuai jika ada perbedaan SUBSTANSIAL (beda HS code, beda jumlah angka, beda negara, beda jenis barang).
- WAJIB tampilkan SETIAP item barang SATU PER SATU di daftar_barang, tanpa terkecuali. Jika ada 40 item, HARUS ada 40 entry. JANGAN skip, JANGAN ringkas, JANGAN tulis "dan seterusnya". Tulis SEMUA dari item pertama sampai terakhir.
- Baca SELURUH halaman dokumen dengan teliti, termasuk halaman-halaman lampiran dan tabel.

PENANGANAN PI PERUBAHAN:
- Jika dokumen adalah PI Perubahan (amandemen/revisi dari PI sebelumnya):
  * Set is_pi_perubahan = true
  * PI baru yang belum dinomori (draft) adalah HAL NORMAL → JANGAN anggap sebagai ketidaksesuaian
  * JANGAN analisa VPTI/LS (set vpti_ls = null) — VPTI/LS hanya untuk PI baru
  * Bandingkan PI Baru (draft) vs Pertek terbaru
  * PI lama hanya sebagai referensi crosscheck perubahan
  * Di kesimpulan, fokus ke: apa saja yang berubah dan berapa jumlahnya
  * TETAP analisa Rencana Distribusi jika API-U dan dokumen distribusi ada

Struktur JSON:

{
  "info": {
    "nomor_pi": "... (tulis 'Draft/Belum dinomori' jika belum ada)",
    "tanggal_pi": "... (tulis 'Draft' jika belum ada)",
    "nomor_pertek": "...",
    "tanggal_pertek": "...",
    "jenis_api": "API-P" atau "API-U",
    "is_pi_perubahan": true atau false
  },

  "identitas_perusahaan": {
    "status": "Sesuai" atau "Tidak Sesuai",
    "items": [
      {"aspek": "Nama", "pi": "...", "pertek": "...", "status": "Sesuai"},
      {"aspek": "NIB", "pi": "...", "pertek": "...", "status": "Sesuai"},
      {"aspek": "Alamat", "pi": "...", "pertek": "...", "status": "Sesuai"}
    ]
  },

  "spesifikasi_barang": {
    "status": "Sesuai" atau "Tidak Sesuai",
    "total_items": 0,
    "total_sesuai": 0,
    "total_tidak_sesuai": 0,
    "daftar_barang": [
      {
        "no": 1,
        "hs_code": "...",
        "uraian": "nama komoditi",
        "spesifikasi": "spesifikasi teknis barang (ukuran, grade, bentuk, dll). Tulis '-' jika tidak ada.",
        "jumlah_pi": "500 ton",
        "jumlah_pertek": "500 ton",
        "status": "Sesuai" atau "Tidak Sesuai",
        "perbedaan": "" atau "singkat: 'Jumlah: PI=500 ton, Pertek=400 ton'"
      }
    ],
    "ringkasan": {
      "hs_code": {"status": "Sesuai", "keterangan": ""},
      "uraian_barang": {"status": "Sesuai", "keterangan": ""},
      "spesifikasi_teknis": {"status": "Sesuai", "keterangan": ""},
      "jumlah_satuan": {"status": "Sesuai", "keterangan": "jika Tidak Sesuai: sebutkan HS mana dan angkanya"},
      "pelabuhan_tujuan": {"status": "Sesuai", "keterangan": ""}
    }
  },

  "data_pi_vs_pertek": {
    "status": "Sesuai" atau "Tidak Sesuai",
    "items": [
      {"aspek": "Nomor Pertek di PI", "pi": "...", "pertek": "...", "status": "Sesuai"},
      {"aspek": "Tanggal Pertek di PI", "pi": "...", "pertek": "...", "status": "Sesuai"},
      {"aspek": "Komoditas", "pi": "...", "pertek": "...", "status": "Sesuai"}
    ]
  },

  "vpti_ls": {
    "jenis_api": "API-P" atau "API-U",
    "kbli": "...",
    "kbli_deskripsi": "...",
    "wajib": true atau false,
    "alasan_singkat": "1 kalimat",
    "pengecualian": [],
    "profil_usaha": "1 kalimat"
  },

  "rencana_distribusi": {
    "ada": true atau false,
    "penandatangan_distribusi": "NAMA ORANG yang menandatangani rencana distribusi (bukan nama perusahaan/produk)",
    "penanggung_jawab_pertek": "NAMA ORANG penanggung jawab di Pertek (bukan nama perusahaan)",
    "penandatangan_sesuai": true atau false,
    "alokasi": [
      {
        "hs_code": "...",
        "uraian": "...",
        "jumlah_distribusi": "jumlah total alokasi di rencana distribusi",
        "jumlah_pertek": "jumlah di Pertek",
        "status": "Sesuai" atau "Tidak Sesuai (melebihi Pertek)"
      }
    ],
    "mitra_pengguna_akhir": [
      {"nama": "nama perusahaan mitra/pengguna akhir", "alamat": "alamat"}
    ],
    "status": "Sesuai" atau "Tidak Sesuai",
    "keterangan": "kosong jika sesuai, atau jelaskan ketidaksesuaian"
  },

  "rekap_data": {
    "daftar_hs": ["list SEMUA HS code yang ada di dokumen"],
    "negara_muat": ["list semua negara muat dari PI"],
    "pelabuhan_tujuan": ["list semua pelabuhan tujuan"]
  },

  "kesimpulan": {
    "status": "DAPAT DIPROSES" atau "TIDAK DAPAT DIPROSES",
    "catatan": "Singkat. Untuk PI Perubahan: sebutkan apa saja yang berubah dan jumlahnya.",
    "ketidaksesuaian": ["list singkat aspek yg tidak sesuai, kosong jika semua sesuai"],
    "perubahan": ["list perubahan dari PI lama ke PI baru, kosong jika bukan PI Perubahan. Contoh: 'HS 7320.20.90: jumlah berubah dari 500 ke 1500 Piece'"]
  }
}

PENTING:
- ANALISIS VPTI/LS (HANYA untuk PI BARU, baik API-P maupun API-U):
  * Jika PI Perubahan: set vpti_ls = null, JANGAN analisa VPTI/LS.
  * Jika PI baru (bukan perubahan), WAJIB analisa VPTI/LS untuk API-P DAN API-U.
  * Periksa pengecualian berikut:
    - Impor ke KPBPB, KEK, atau TPB
    - Fasilitas KITE Pembebasan untuk tujuan ekspor
    - API-P industri otomotif, elektronika, galangan kapal, mould & dies, pesawat terbang, atau alat berat
    - API-P berstatus AEO atau MITA Kepabeanan
    - API-P pengguna SKVI USDFS
    - Penerima fasilitas BMDTP
    - Kontraktor KKS Migas, Kontrak Karya, atau proyek ketenagalistrikan/kepentingan umum tertentu
    - Barang HS 7213.91.30, 7213.91.90, 7213.99.90 (C > 0,6%), 7225.50.90 (TMBP)
  * Pola analisis: tidak hanya melihat nama KBLI, tetapi juga uraian KBLI pada NIB/Pertek, profil/website resmi perusahaan, kegiatan usaha/manufaktur yang dijalankan, dan penjelasan Kemenperin jika tersedia.

- RENCANA DISTRIBUSI (WAJIB untuk API-U, termasuk saat PI Perubahan):
  * Jika API-U, SELALU cek rencana distribusi meskipun PI Perubahan.
  * CARA MENDETEKSI: Cari di SEMUA halaman PDF yang diupload. Dokumen rencana distribusi biasanya berupa tabel/halaman terpisah dengan judul "RENCANA DISTRIBUSI" atau "Rencana Distribusi Tahun ..." yang berisi tabel alokasi barang ke mitra/pengguna akhir. Bisa berada di file PDF mana saja (bukan hanya di PI atau Pertek). BACA SEMUA HALAMAN dengan teliti.
  * Jika ditemukan dokumen rencana distribusi di salah satu PDF:
    1. Set rencana_distribusi.ada = true
    2. CARI PENANDATANGAN: Penandatangan adalah NAMA ORANG (bukan nama perusahaan, bukan nama produk/barang). Biasanya ada di BAGIAN BAWAH dokumen rencana distribusi, di bawah tanda tangan/cap/meterai. Formatnya: "(Nama Orang)" diikuti jabatan seperti "Direktur" atau "Direktur Utama". Contoh benar: "Tee Susanto", "Setia Diarta". Contoh SALAH: "PT. Atamora" (ini nama perusahaan), "CLUTCH HSG" (ini nama produk). WAJIB isi dengan NAMA ORANG, JANGAN nama perusahaan/produk.
    3. PENANGGUNG JAWAB PERTEK: Cari di dokumen Pertek bagian "Penanggung Jawab" atau "Penanggungjawab". Ini juga NAMA ORANG, bukan nama perusahaan. Biasanya tercantum di halaman awal Pertek.
    4. Bandingkan penandatangan distribusi dengan penanggung jawab Pertek. Jika NAMA ORANG-nya berbeda → Tidak Sesuai.
    5. Total alokasi per item di rencana distribusi HARUS SAMA ATAU LEBIH KECIL dari jumlah di Pertek. Jika melebihi → Tidak Sesuai.
    6. Tampilkan daftar mitra/pengguna akhir beserta alamatnya.
  * HANYA set rencana_distribusi.ada = false jika benar-benar TIDAK ADA dokumen rencana distribusi di seluruh PDF yang diupload.
  * Jika bukan API-U, set rencana_distribusi.ada = false.

- Jika data tidak tersedia, tulis "Tidak tersedia"
- PI draft tanpa nomor/tanggal BUKAN ketidaksesuaian jika is_pi_perubahan = true.
- INGAT: beda format/urutan penulisan BUKAN berarti Tidak Sesuai. Fokus pada isi/makna.
- JANGAN cocokkan Negara Muat. Yang dicocokkan HANYA Pelabuhan Tujuan.
- WAJIB isi rekap_data.
- KRITIS: daftar_barang HARUS berisi SELURUH item yang ada di dokumen, dari nomor 1 sampai terakhir. Jika dokumen punya 40 item maka daftar_barang HARUS punya 40 entry. Tidak boleh diringkas atau diwakilkan."""


def build_user_prompt(pdf_texts):
    prompt = "Berikut dokumen-dokumen yang perlu dianalisis:\n\n"
    for item in pdf_texts:
        prompt += f"{'='*60}\n"
        prompt += f"DOKUMEN: {item['name']} ({item['pages']} halaman)\n"
        prompt += f"{'='*60}\n"
        prompt += item["text"] + "\n\n"
    prompt += "Identifikasi mana PI dan mana Pertek, lalu cocokkan seluruh data. Output JSON saja."
    return prompt


# ============================================================
# CALL GEMINI API
# ============================================================
def call_gemini(api_key, model, system_prompt, user_prompt):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 65536,
            "responseMimeType": "application/json",
            "thinkingConfig": {"thinkingBudget": 0},
        }
    }
    return requests.post(url, json=payload, timeout=240)


def analyze_documents(api_key, pdf_texts):
    user_prompt = build_user_prompt(pdf_texts)
    models = ["gemini-3.1-flash-lite"]
    last_error = ""

    for model in models:
        for attempt in range(4):
            try:
                response = call_gemini(api_key, model, SYSTEM_PROMPT, user_prompt)
            except requests.exceptions.Timeout:
                last_error = "Request timeout. Server terlalu lama merespon."
                if attempt < 3:
                    time.sleep((attempt + 1) * 5)
                    continue
                else:
                    break
            except requests.exceptions.ConnectionError:
                last_error = "Tidak bisa terhubung ke server Gemini."
                if attempt < 3:
                    time.sleep((attempt + 1) * 5)
                    continue
                else:
                    break

            if response.status_code == 429:
                last_error = "Rate limit tercapai (terlalu banyak request). Tunggu sebentar."
                if attempt < 3:
                    time.sleep((attempt + 1) * 15)
                    continue
                else:
                    break

            if response.status_code in (500, 502, 503):
                last_error = f"Server Gemini error (kode {response.status_code})."
                if attempt < 3:
                    time.sleep((attempt + 1) * 5)
                    continue
                else:
                    break

            response.raise_for_status()
            data = response.json()
            candidate = data["candidates"][0]

            if candidate.get("finishReason") == "MAX_TOKENS":
                raise ValueError("Response terpotong. Coba upload lebih sedikit file.")

            response_text = candidate["content"]["parts"][0]["text"].strip()
            if response_text.startswith("```"):
                lines = response_text.split("\n")
                lines = [l for l in lines if not l.strip().startswith("```")]
                response_text = "\n".join(lines)

            return json.loads(response_text)

    raise ValueError(f"Gagal setelah beberapa percobaan. {last_error} Coba lagi dalam 1-2 menit.")


# ============================================================
# POST-PROCESSING: FALLBACK DARI TEKS PDF
# ============================================================
import re


def _find_rencana_distribusi_in_texts(pdf_texts):
    """Cari dokumen rencana distribusi di semua PDF text."""
    for item in pdf_texts:
        text_lower = item["text"].lower()
        if "rencana distribusi" in text_lower:
            return item["text"]
    return None


def _is_person_name(name):
    """Cek apakah string kemungkinan nama orang (bukan perusahaan/produk)."""
    name_upper = name.upper().strip()
    # Bukan nama orang jika mengandung kata-kata ini
    company_keywords = ["PT", "PT.", "CV", "CV.", "UD", "UD.", "CORP", "INC", "LTD",
                        "INDUSTRI", "MAKMUR", "SENTOSA", "JAYA", "TEHNIK", "TEKNIK",
                        "STEEL", "IRON", "METAL", "COIL", "WIRE", "PIPE", "SHEET",
                        "CLUTCH", "BEARING", "BOLT", "NUT", "SPRING", "HSG"]
    for kw in company_keywords:
        if kw in name_upper.split():
            return False
    # Nama orang biasanya 2-4 kata, tiap kata diawali huruf besar
    words = name.strip().split()
    if len(words) < 1 or len(words) > 5:
        return False
    return True


def _extract_penandatangan_from_text(text):
    """Cari nama penandatangan (ORANG) di dokumen rencana distribusi.
    Biasanya format: (Nama Orang) diikuti jabatan seperti Direktur."""
    patterns = [
        # (Nama Orang)\nDirektur
        r'\(([A-Z][a-zA-Z\s\.]+)\)\s*\n?\s*(?:Direktur|Director|Pimpinan|Manager|Komisaris)',
        # Direktur\n\nNama Orang  atau  Direktur,\nNama Orang
        r'(?:Direktur|Director|Pimpinan|Direktur Utama)\s*[,:]?\s*\n\s*\n?\s*([A-Z][a-zA-Z\s\.]+)',
        # (Nama Orang) di akhir baris
        r'\(([A-Z][a-zA-Z\s\.]+)\)\s*$',
    ]
    for pattern in patterns:
        matches = re.findall(pattern, text, re.MULTILINE | re.IGNORECASE)
        if matches:
            # Cari dari bawah ke atas (tanda tangan biasanya di bawah)
            for name in reversed(matches):
                name = name.strip()
                if len(name) > 3 and len(name) < 50 and _is_person_name(name):
                    return name
    return None


def postprocess_result(result, pdf_texts):
    """Perbaiki hasil AI dengan fallback dari teks PDF langsung."""
    info = result.get("info", {})
    jenis_api = info.get("jenis_api", "")
    dist = result.get("rencana_distribusi") or {}

    # Fallback 1: Rencana distribusi ada di PDF tapi AI bilang tidak ada
    if "API-U" in str(jenis_api).upper():
        rd_text = _find_rencana_distribusi_in_texts(pdf_texts)
        if rd_text and not dist.get("ada"):
            dist["ada"] = True
            dist.setdefault("status", "Perlu diperiksa manual")
            dist.setdefault("keterangan", "Dokumen rencana distribusi ditemukan di PDF tapi AI gagal menganalisis. Periksa manual.")
            dist.setdefault("alokasi", [])
            dist.setdefault("mitra_pengguna_akhir", [])
            result["rencana_distribusi"] = dist

        # Fallback 2: Validasi penandatangan — harus nama orang, bukan perusahaan/produk
        if dist.get("ada"):
            penandatangan = dist.get("penandatangan_distribusi", "")
            penandatangan_valid = (
                penandatangan
                and penandatangan.lower() not in ("tidak tersedia", "-", "")
                and _is_person_name(penandatangan)
            )

            if not penandatangan_valid:
                # Coba cari dari teks PDF
                found_name = None
                if rd_text:
                    found_name = _extract_penandatangan_from_text(rd_text)

                if found_name:
                    dist["penandatangan_distribusi"] = found_name
                    penandatangan_valid = True
                else:
                    # Nama salah (perusahaan/produk) atau tidak ditemukan → ganti pesan jelas
                    dist["penandatangan_distribusi"] = "Periksa manual di dokumen rencana distribusi"

            # Re-check kesesuaian
            if penandatangan_valid:
                pj_pertek = dist.get("penanggung_jawab_pertek", "")
                if pj_pertek and pj_pertek.lower() not in ("tidak tersedia", "-", ""):
                    dist["penandatangan_sesuai"] = (
                        dist["penandatangan_distribusi"].lower().strip() == pj_pertek.lower().strip()
                    )

            # Validasi penanggung_jawab_pertek juga harus nama orang
            pj = dist.get("penanggung_jawab_pertek", "")
            if pj and pj.lower() not in ("tidak tersedia", "-", "") and not _is_person_name(pj):
                dist["penanggung_jawab_pertek"] = "Periksa manual di dokumen Pertek"

    return result


# ============================================================
# RENDER RESULTS
# ============================================================
def status_badge(status):
    if "Sesuai" in str(status) and "Tidak" not in str(status):
        return f'<span class="badge-sesuai">&#9989; {status}</span>'
    elif "Tidak Sesuai" in str(status):
        return f'<span class="badge-tidak">&#10060; {status}</span>'
    return status


def render_results(result):
    info = result.get("info", {})
    is_perubahan = info.get("is_pi_perubahan", False)

    # Info bar
    pi_label = "PI (Draft)" if is_perubahan and "Draft" in str(info.get("nomor_pi", "")) else "PI"
    st.markdown(f"""<div class="info-bar">
        <strong>{pi_label}:</strong> {info.get('nomor_pi', '-')} ({info.get('tanggal_pi', '-')})
        &nbsp;&nbsp;|&nbsp;&nbsp;
        <strong>Pertek:</strong> {info.get('nomor_pertek', '-')} ({info.get('tanggal_pertek', '-')})
        &nbsp;&nbsp;|&nbsp;&nbsp;
        <strong>Jenis:</strong> {info.get('jenis_api', '-')}
        {' &nbsp;&nbsp;|&nbsp;&nbsp; <strong style="color:#b45309;">PI Perubahan</strong>' if is_perubahan else ''}
    </div>""", unsafe_allow_html=True)

    # 1. Identitas Perusahaan
    id_data = result.get("identitas_perusahaan", {})
    id_status = id_data.get("status", "N/A")
    if "Tidak" in str(id_status):
        id_status_label = "Tidak Sesuai"
    elif "Sesuai" in str(id_status):
        id_status_label = "Sesuai"
    else:
        id_status_label = id_status
    with st.expander(f"1. Identitas Perusahaan — {id_status_label}", expanded=False):
        id_items = id_data.get("items", [])
        if id_items:
            html = '<table class="detail-table">'
            html += '<tr><th>Aspek</th><th>PI</th><th>Pertek</th><th>Status</th></tr>'
            for item in id_items:
                html += f'<tr><td>{item.get("aspek", "")}</td><td>{item.get("pi", "")}</td><td>{item.get("pertek", "")}</td><td>{status_badge(item.get("status", ""))}</td></tr>'
            html += '</table>'
            st.markdown(html, unsafe_allow_html=True)

    # 2. Spesifikasi Barang
    spec_data = result.get("spesifikasi_barang", {})
    total = spec_data.get("total_items", 0)
    total_ok = spec_data.get("total_sesuai", 0)
    total_bad = spec_data.get("total_tidak_sesuai", 0)

    with st.expander(f"2. Spesifikasi Barang ({total_ok}/{total} sesuai)", expanded=True):
        # Tampilkan SEMUA item barang
        daftar_barang = spec_data.get("daftar_barang", [])
        if daftar_barang:
            html = '<table class="detail-table">'
            html += '<tr><th>No</th><th>HS Code</th><th>Komoditi</th><th>Spesifikasi</th><th>Jumlah PI</th><th>Jumlah Pertek</th><th>Status</th></tr>'
            for item in daftar_barang:
                status_val = item.get("status", "")
                if "Tidak" in str(status_val):
                    row_style = 'style="background:#fef2f2;"'
                    perbedaan = f'<br><small style="color:#991b1b;">{item.get("perbedaan", "")}</small>' if item.get("perbedaan") else ""
                else:
                    row_style = ''
                    perbedaan = ""
                spesifikasi = item.get("spesifikasi", "-")
                html += f'<tr {row_style}><td>{item.get("no", "")}</td><td><strong>{item.get("hs_code", "")}</strong></td><td>{item.get("uraian", "")}{perbedaan}</td><td>{spesifikasi}</td><td>{item.get("jumlah_pi", "-")}</td><td>{item.get("jumlah_pertek", "-")}</td><td>{status_badge(status_val)}</td></tr>'
            html += '</table>'
            st.markdown(html, unsafe_allow_html=True)
        else:
            # Fallback: tampilkan items_tidak_sesuai jika daftar_barang kosong
            tidak_sesuai_items = spec_data.get("items_tidak_sesuai", [])
            if tidak_sesuai_items:
                html = '<table class="detail-table">'
                html += '<tr><th>HS Code</th><th>Uraian</th><th>Perbedaan</th></tr>'
                for item in tidak_sesuai_items:
                    html += f'<tr><td><strong>{item.get("hs_code", "")}</strong></td><td>{item.get("uraian", "")}</td><td style="color:#991b1b;">{item.get("perbedaan", "")}</td></tr>'
                html += '</table>'
                st.markdown(html, unsafe_allow_html=True)
            elif total > 0:
                st.success(f"Seluruh {total} item sesuai")

    # 3. Data PI vs Pertek
    pi_data = result.get("data_pi_vs_pertek", {})
    pi_status = pi_data.get("status", "N/A")
    if "Tidak" in str(pi_status):
        pi_status_label = "Tidak Sesuai"
    elif "Sesuai" in str(pi_status):
        pi_status_label = "Sesuai"
    else:
        pi_status_label = pi_status
    with st.expander(f"3. Data PI vs Pertek — {pi_status_label}", expanded=False):
        pi_items = pi_data.get("items", [])
        if pi_items:
            html = '<table class="detail-table">'
            html += '<tr><th>Aspek</th><th>PI</th><th>Pertek</th><th>Status</th></tr>'
            for item in pi_items:
                html += f'<tr><td>{item.get("aspek", "")}</td><td>{item.get("pi", "")}</td><td>{item.get("pertek", "")}</td><td>{status_badge(item.get("status", ""))}</td></tr>'
            html += '</table>'
            st.markdown(html, unsafe_allow_html=True)

    # 4. Rencana Distribusi
    dist_data = result.get("rencana_distribusi") or {}
    if dist_data.get("ada"):
        dist_status = dist_data.get("status", "N/A")
        with st.expander(f"4. Rencana Distribusi — {dist_status}", expanded=False):
            # Penandatangan
            penandatangan = dist_data.get("penandatangan_distribusi", "-")
            pj_pertek = dist_data.get("penanggung_jawab_pertek", "-")
            tanda_sesuai = dist_data.get("penandatangan_sesuai", True)

            # Cek apakah perlu manual review
            perlu_manual = "periksa manual" in penandatangan.lower() or "periksa manual" in pj_pertek.lower()

            if perlu_manual:
                tanda_badge = '<span style="color:#b45309;font-weight:600;">Periksa Manual</span>'
            elif tanda_sesuai:
                tanda_badge = '<span style="color:#166534;font-weight:600;">Sesuai</span>'
            else:
                tanda_badge = '<span style="color:#991b1b;font-weight:600;">Tidak Sesuai ⚠️</span>'

            st.markdown(f"**Penandatangan Distribusi:** {penandatangan}")
            st.markdown(f"**Penanggung Jawab Pertek:** {pj_pertek} — {tanda_badge}", unsafe_allow_html=True)

            if perlu_manual:
                st.warning("Nama penandatangan tidak dapat dideteksi otomatis. Silakan periksa dokumen rencana distribusi dan Pertek secara manual.")

            # Alokasi
            alokasi = dist_data.get("alokasi", [])
            if alokasi:
                st.markdown("**Alokasi:**")
                html = '<table class="detail-table">'
                html += '<tr><th>HS Code</th><th>Uraian</th><th>Jml Distribusi</th><th>Jml Pertek</th><th>Status</th></tr>'
                for a in alokasi:
                    html += f'<tr><td><strong>{a.get("hs_code", "")}</strong></td><td>{a.get("uraian", "")}</td><td>{a.get("jumlah_distribusi", "-")}</td><td>{a.get("jumlah_pertek", "-")}</td><td>{status_badge(a.get("status", ""))}</td></tr>'
                html += '</table>'
                st.markdown(html, unsafe_allow_html=True)

            # Mitra
            mitra_list = dist_data.get("mitra_pengguna_akhir", [])
            if mitra_list:
                st.markdown("**Mitra/Pengguna Akhir:**")
                html = '<table class="detail-table">'
                html += '<tr><th>Nama</th><th>Alamat</th></tr>'
                for m in mitra_list:
                    html += f'<tr><td>{m.get("nama", "-")}</td><td>{m.get("alamat", "-")}</td></tr>'
                html += '</table>'
                st.markdown(html, unsafe_allow_html=True)

            ket = dist_data.get("keterangan", "")
            if ket:
                st.warning(ket)

    # 5. VPTI/LS — hanya untuk PI baru (baik API-P maupun API-U)
    vpti_data = result.get("vpti_ls") or {}
    if vpti_data and not is_perubahan:
        with st.expander("5. Analisis VPTI/LS", expanded=False):
            vpti_html = '<table class="detail-table">'
            vpti_rows = [
                ("Jenis API", vpti_data.get("jenis_api", "-")),
                ("KBLI", f"{vpti_data.get('kbli', '-')} - {vpti_data.get('kbli_deskripsi', '')}"),
                ("Profil Usaha", vpti_data.get("profil_usaha", "-")),
            ]
            if vpti_data.get("pengecualian"):
                vpti_rows.append(("Pengecualian", ", ".join(vpti_data["pengecualian"])))
            vpti_rows.append(("Alasan", vpti_data.get("alasan_singkat", "-")))

            for label, val in vpti_rows:
                vpti_html += f'<tr><td style="width:30%;font-weight:600;">{label}</td><td>{val}</td></tr>'
            vpti_html += '</table>'
            st.markdown(vpti_html, unsafe_allow_html=True)

            wajib = vpti_data.get("wajib", True)
            if wajib:
                st.warning("**Wajib VPTI/LS**")
            else:
                st.success("**Tidak wajib VPTI/LS**")

    # ============================================================
    # KESIMPULAN AKHIR TABLE
    # ============================================================
    st.markdown("---")
    st.markdown("### Kesimpulan Akhir")

    rows = []
    rows.append(("Identitas perusahaan", id_data.get("status", "N/A"), ""))

    ringkasan = spec_data.get("ringkasan", {})
    for key, label in [
        ("hs_code", "HS Code"),
        ("uraian_barang", "Uraian barang"),
        ("spesifikasi_teknis", "Spesifikasi teknis"),
        ("jumlah_satuan", "Jumlah dan satuan"),
        ("pelabuhan_tujuan", "Pelabuhan tujuan"),
    ]:
        val = ringkasan.get(key, "N/A")
        if isinstance(val, dict):
            rows.append((label, val.get("status", "N/A"), val.get("keterangan", "")))
        else:
            rows.append((label, val, ""))

    rows.append(("Data PI vs Pertek", pi_data.get("status", "N/A"), ""))

    if dist_data.get("ada"):
        rows.append(("Rencana Distribusi", dist_data.get("status", "N/A"), dist_data.get("keterangan", "")))

    if vpti_data and not is_perubahan:
        wajib = vpti_data.get("wajib", True)
        rows.append(("Kewajiban VPTI/LS", "Wajib VPTI/LS" if wajib else "Tidak wajib VPTI/LS", ""))

    html = '<table class="summary-table">'
    html += '<tr><th style="width:35%">Aspek Pemeriksaan</th><th>Hasil</th></tr>'

    for label, status, keterangan in rows:
        if "Sesuai" in str(status) and "Tidak" not in str(status):
            display = f'&#9989; <strong>Sesuai</strong>'
        elif "Tidak Sesuai" in str(status):
            display = f'&#10060; <strong>Tidak Sesuai</strong>'
            if keterangan:
                display += f'<br><small style="color:#64748b;">{keterangan}</small>'
        elif "Tidak wajib" in str(status):
            display = f'&#9989; {status}'
        elif "Wajib" in str(status):
            display = f'&#9989; {status}'
        else:
            display = status

        html += f'<tr><td>{label}</td><td>{display}</td></tr>'

    html += '</table>'
    st.markdown(html, unsafe_allow_html=True)

    # Kesimpulan
    kesimpulan = result.get("kesimpulan", {})
    status_str = kesimpulan.get("status", "DAPAT DIPROSES")
    catatan = kesimpulan.get("catatan", "")
    ketidaksesuaian = kesimpulan.get("ketidaksesuaian", [])
    perubahan = kesimpulan.get("perubahan", [])

    if "DAPAT" in status_str and "TIDAK" not in status_str:
        html = f'<div class="conclusion-card conclusion-dapat">'
        html += f'<strong style="color:#166534;font-size:1.1rem;">&#9989; {status_str}</strong>'
        if catatan:
            html += f'<br><span style="color:#15803d;font-size:0.9rem;">{catatan}</span>'
        if perubahan:
            html += '<br><br><strong style="color:#166534;">Perubahan:</strong><ul style="margin:0.3rem 0 0 1.2rem;color:#15803d;">'
            for p in perubahan:
                html += f'<li>{p}</li>'
            html += '</ul>'
        html += '</div>'
        st.markdown(html, unsafe_allow_html=True)
    else:
        html = f'<div class="conclusion-card conclusion-tidak">'
        html += f'<strong style="color:#991b1b;font-size:1.1rem;">&#10060; {status_str}</strong>'
        if catatan:
            html += f'<br><span style="color:#b91c1c;font-size:0.9rem;">{catatan}</span>'
        if ketidaksesuaian:
            html += '<br><br><strong style="color:#991b1b;">Ketidaksesuaian:</strong><ul style="margin:0.3rem 0 0 1.2rem;color:#991b1b;">'
            for k in ketidaksesuaian:
                html += f'<li>{k}</li>'
            html += '</ul>'
        if perubahan:
            html += '<br><strong style="color:#991b1b;">Perubahan:</strong><ul style="margin:0.3rem 0 0 1.2rem;color:#991b1b;">'
            for p in perubahan:
                html += f'<li>{p}</li>'
            html += '</ul>'
        html += '</div>'
        st.markdown(html, unsafe_allow_html=True)

    return rows


def build_download_text(result, rows):
    lines = []
    lines.append("LAPORAN PENCOCOKAN PI DAN PERTEK")
    lines.append(f"Tanggal: {datetime.now().strftime('%d %B %Y %H:%M')}")
    lines.append("=" * 60)

    info = result.get("info", {})
    lines.append(f"PI: {info.get('nomor_pi', '-')} ({info.get('tanggal_pi', '-')})")
    lines.append(f"Pertek: {info.get('nomor_pertek', '-')} ({info.get('tanggal_pertek', '-')})")
    lines.append(f"Jenis: {info.get('jenis_api', '-')}")
    lines.append("")

    lines.append("1. IDENTITAS PERUSAHAAN")
    id_data = result.get("identitas_perusahaan", {})
    for item in id_data.get("items", []):
        lines.append(f"   {item.get('aspek', '')}: PI={item.get('pi', '')} | Pertek={item.get('pertek', '')} -> {item.get('status', '')}")
    lines.append("")

    spec_data = result.get("spesifikasi_barang", {})
    total = spec_data.get("total_items", 0)
    total_ok = spec_data.get("total_sesuai", 0)
    lines.append(f"2. SPESIFIKASI BARANG ({total_ok}/{total} sesuai)")
    daftar_barang = spec_data.get("daftar_barang", [])
    if daftar_barang:
        for item in daftar_barang:
            status_txt = item.get("status", "")
            spec_txt = item.get("spesifikasi", "-")
            line = f"   {item.get('no', '')}. HS {item.get('hs_code', '')} | {item.get('uraian', '')} | Spec: {spec_txt} | PI: {item.get('jumlah_pi', '-')} | Pertek: {item.get('jumlah_pertek', '-')} | {status_txt}"
            if item.get("perbedaan"):
                line += f" ({item['perbedaan']})"
            lines.append(line)
    else:
        tidak_sesuai = spec_data.get("items_tidak_sesuai", [])
        if tidak_sesuai:
            lines.append("   Item tidak sesuai:")
            for item in tidak_sesuai:
                lines.append(f"   - HS {item.get('hs_code', '')}: {item.get('perbedaan', '')}")
        else:
            lines.append("   Seluruh item sesuai")
    lines.append("")

    lines.append("3. DATA PI VS PERTEK")
    pi_data = result.get("data_pi_vs_pertek", {})
    for item in pi_data.get("items", []):
        lines.append(f"   {item.get('aspek', '')}: PI={item.get('pi', '')} | Pertek={item.get('pertek', '')} -> {item.get('status', '')}")
    lines.append("")

    dist_data = result.get("rencana_distribusi") or {}
    if dist_data.get("ada"):
        lines.append("4. RENCANA DISTRIBUSI")
        lines.append(f"   Penandatangan: {dist_data.get('penandatangan_distribusi', '-')}")
        lines.append(f"   Penanggung Jawab Pertek: {dist_data.get('penanggung_jawab_pertek', '-')}")
        lines.append(f"   Penandatangan sesuai: {'Ya' if dist_data.get('penandatangan_sesuai') else 'TIDAK'}")
        for a in dist_data.get("alokasi", []):
            lines.append(f"   Alokasi HS {a.get('hs_code', '')}: Distribusi={a.get('jumlah_distribusi', '-')} | Pertek={a.get('jumlah_pertek', '-')} -> {a.get('status', '')}")
        for m in dist_data.get("mitra_pengguna_akhir", []):
            lines.append(f"   Mitra: {m.get('nama', '-')} ({m.get('alamat', '-')})")
        lines.append(f"   Status: {dist_data.get('status', '-')}")
        ket = dist_data.get("keterangan", "")
        if ket:
            lines.append(f"   Keterangan: {ket}")
        lines.append("")

    vpti_data = result.get("vpti_ls")
    if vpti_data:
        lines.append("5. ANALISIS VPTI/LS")
        lines.append(f"   Jenis API: {vpti_data.get('jenis_api', '')}")
        lines.append(f"   KBLI: {vpti_data.get('kbli', '')} - {vpti_data.get('kbli_deskripsi', '')}")
        lines.append(f"   Wajib: {'Ya' if vpti_data.get('wajib', True) else 'Tidak'}")
        lines.append(f"   Alasan: {vpti_data.get('alasan_singkat', '')}")
        lines.append("")
    else:
        lines.append("5. ANALISIS VPTI/LS")
        lines.append("   (Tidak berlaku - PI Perubahan)")
        lines.append("")

    lines.append("=" * 60)
    lines.append("KESIMPULAN AKHIR")
    lines.append("=" * 60)
    for label, status, keterangan in rows:
        line = f"  {label}: {status}"
        if keterangan:
            line += f" ({keterangan})"
        lines.append(line)
    lines.append("")

    kesimpulan = result.get("kesimpulan", {})
    lines.append(kesimpulan.get("status", ""))
    if kesimpulan.get("catatan"):
        lines.append(f"Catatan: {kesimpulan['catatan']}")
    ketidaksesuaian = kesimpulan.get("ketidaksesuaian", [])
    if ketidaksesuaian:
        lines.append("Ketidaksesuaian:")
        for k in ketidaksesuaian:
            lines.append(f"  - {k}")
    perubahan = kesimpulan.get("perubahan", [])
    if perubahan:
        lines.append("Perubahan:")
        for p in perubahan:
            lines.append(f"  - {p}")

    return "\n".join(lines)


# ============================================================
# REKAP TAB HELPERS
# ============================================================
def _split_and_count(series):
    """Split comma-separated values and count occurrences."""
    items = []
    for val in series.dropna():
        items.extend([x.strip() for x in str(val).split(",") if x.strip()])
    if not items:
        return pd.DataFrame(columns=["Item", "Jumlah"])
    counts = pd.Series(items).value_counts().reset_index()
    counts.columns = ["Item", "Jumlah"]
    return counts


def render_rekap_tab():
    st.markdown('<div class="section-header">Rekap Data Analisis</div>', unsafe_allow_html=True)

    df = load_from_sheets()
    if df is None or df.empty:
        st.info("Belum ada data analisis tersimpan. Data akan muncul setelah analisis pertama.")
        return

    df["tanggal_analisis"] = pd.to_datetime(df["tanggal_analisis"], errors="coerce")

    # Period filter
    period = st.selectbox("Periode", ["Minggu Ini", "Bulan Ini", "Tahun Ini", "Semua Data"])
    now = datetime.now()

    if period == "Minggu Ini":
        start = now - timedelta(days=now.weekday())
        mask = df["tanggal_analisis"] >= start.strftime("%Y-%m-%d")
    elif period == "Bulan Ini":
        mask = (df["tanggal_analisis"].dt.month == now.month) & (df["tanggal_analisis"].dt.year == now.year)
    elif period == "Tahun Ini":
        mask = df["tanggal_analisis"].dt.year == now.year
    else:
        mask = pd.Series(True, index=df.index)

    dff = df[mask].copy()

    if dff.empty:
        st.warning("Tidak ada data untuk periode ini.")
        return

    # Summary metrics
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total Dokumen", len(dff))
    with col2:
        dapat = dff["status"].astype(str).str.contains("DAPAT", case=False, na=False) & ~dff["status"].astype(str).str.contains("TIDAK", case=False, na=False)
        st.metric("Dapat Diproses", int(dapat.sum()))
    with col3:
        tidak = dff["status"].astype(str).str.contains("TIDAK", case=False, na=False)
        st.metric("Tidak Dapat Diproses", int(tidak.sum()))

    # HS Codes
    st.markdown('<div class="section-header">HS Code</div>', unsafe_allow_html=True)
    hs_df = _split_and_count(dff["hs_codes"])
    if not hs_df.empty:
        st.dataframe(hs_df, use_container_width=True, hide_index=True)
    else:
        st.caption("Tidak ada data HS Code.")

    # Negara Muat
    st.markdown('<div class="section-header">Negara Muat</div>', unsafe_allow_html=True)
    neg_df = _split_and_count(dff["negara_muat"])
    if not neg_df.empty:
        st.dataframe(neg_df, use_container_width=True, hide_index=True)
    else:
        st.caption("Tidak ada data negara muat.")

    # Pelabuhan Tujuan
    st.markdown('<div class="section-header">Pelabuhan Tujuan</div>', unsafe_allow_html=True)
    pel_df = _split_and_count(dff["pelabuhan_tujuan"])
    if not pel_df.empty:
        st.dataframe(pel_df, use_container_width=True, hide_index=True)
    else:
        st.caption("Tidak ada data pelabuhan.")

    # Jenis API breakdown
    st.markdown('<div class="section-header">Jenis API</div>', unsafe_allow_html=True)
    api_counts = dff["jenis_api"].value_counts().reset_index()
    api_counts.columns = ["Jenis API", "Jumlah"]
    if not api_counts.empty:
        st.dataframe(api_counts, use_container_width=True, hide_index=True)

    # Detail table
    st.markdown('<div class="section-header">Detail Analisis</div>', unsafe_allow_html=True)
    detail_cols = ["tanggal_analisis", "nomor_pi", "nomor_pertek", "nama_perusahaan", "jenis_api", "hs_codes", "negara_muat", "pelabuhan_tujuan", "status"]
    available_cols = [c for c in detail_cols if c in dff.columns]
    detail = dff[available_cols].copy()
    detail["tanggal_analisis"] = detail["tanggal_analisis"].dt.strftime("%d/%m/%Y %H:%M")
    st.dataframe(detail, use_container_width=True, hide_index=True)


# ============================================================
# MAIN UI
# ============================================================
if not check_password():
    st.stop()

# Header
st.markdown("""<div class="header-card">
    <h1>Pertek Checker</h1>
    <p>Upload PDF PI dan Pertek, otomatis dianalisis dan dicocokkan</p>
</div>""", unsafe_allow_html=True)

tab_analisis, tab_rekap = st.tabs(["Analisis", "Rekap Data"])

# ──────────────────────────────────────────────
# TAB 1: ANALISIS
# ──────────────────────────────────────────────
with tab_analisis:
    api_key = load_api_key()

    if "clear_files" not in st.session_state:
        st.session_state["clear_files"] = 0

    uploaded_files = st.file_uploader(
        "Upload file PDF (PI dan Pertek, bisa lebih dari 1)",
        type=["pdf"],
        accept_multiple_files=True,
        key=f"pdf_uploader_{st.session_state['clear_files']}",
    )

    if uploaded_files:
        pdf_texts = []
        total_pages_all = 0
        for f in uploaded_files:
            txt, pages = extract_pdf_text(f)
            total_pages_all += pages
            pdf_texts.append({"name": f.name, "text": txt, "pages": pages})

        file_names = ", ".join([f"{p['name']} ({p['pages']}hal)" for p in pdf_texts])
        st.markdown(f'<div class="file-count">&#128196; {len(uploaded_files)} file diupload ({total_pages_all} halaman total): {file_names}</div>', unsafe_allow_html=True)

        large_files = [p for p in pdf_texts if p["pages"] > 500]
        if large_files:
            names = ", ".join([f"**{p['name']}** ({p['pages']} hal)" for p in large_files])
            st.warning(f"File besar terdeteksi: {names}. Hanya 500 halaman pertama yang dibaca untuk menjaga performa.")

        with st.expander("Lihat teks yang diekstrak dari PDF", expanded=False):
            for item in pdf_texts:
                st.markdown(f"**{item['name']}** ({item['pages']} halaman)")
                st.text(item["text"][:8000] + ("..." if len(item["text"]) > 8000 else ""))
                st.divider()

        # Estimasi biaya
        total_chars = sum(len(p["text"]) for p in pdf_texts)
        est_tokens = total_chars // 3  # ~3 chars per token
        est_input_cost = est_tokens * 0.075 / 1_000_000  # gemini-3.1-flash-lite: $0.075/1M input
        est_output_cost = 0.3 / 1_000_000 * 3000  # estimasi ~3k output tokens, $0.3/1M
        est_total_usd = est_input_cost + est_output_cost
        est_total_idr = est_total_usd * 16500
        st.caption(f"Estimasi: ~{est_tokens:,} token input | Biaya: ~Rp {est_total_idr:,.0f}")

        st.markdown("")
        col1, col2 = st.columns([3, 1])
        with col1:
            analyze_btn = st.button("Analisis & Cocokkan", type="primary", use_container_width=True, disabled=not api_key)
        with col2:
            clear_btn = st.button("Analisis Baru", use_container_width=True)

        if clear_btn:
            st.session_state["clear_files"] += 1
            st.session_state.pop("analysis_result", None)
            st.rerun()

        if not api_key:
            st.error("API key belum dikonfigurasi. Hubungi administrator.")

        if analyze_btn and api_key:
            with st.spinner("Menganalisis dokumen..."):
                try:
                    result = analyze_documents(api_key, pdf_texts)
                    result = postprocess_result(result, pdf_texts)
                    st.session_state["analysis_result"] = result
                    if save_to_sheets(result):
                        st.toast("Data tersimpan ke rekap", icon="✅")
                    else:
                        st.toast("Gagal simpan ke rekap (cek konfigurasi Google Sheets)", icon="⚠️")
                except json.JSONDecodeError:
                    st.error("Error parsing hasil analisis. Coba klik Analisis lagi.")
                except requests.exceptions.HTTPError as e:
                    sc = e.response.status_code if e.response else 0
                    body = ""
                    try:
                        body = e.response.json().get("error", {}).get("message", "") if e.response else ""
                    except Exception:
                        pass
                    if sc in (401, 403):
                        st.error(f"API Key tidak valid atau belum diaktifkan. {body}")
                    elif sc == 429:
                        st.error("Rate limit tercapai. Tunggu 1 menit lalu coba lagi.")
                    elif sc >= 500:
                        st.error("Server sedang sibuk. Tunggu beberapa detik lalu coba lagi.")
                    else:
                        st.error(f"Terjadi error (kode {sc}). {body}")
                except ValueError as e:
                    st.error(str(e))
                except Exception as e:
                    st.error(f"Terjadi error: {str(e)[:200]}")

        if "analysis_result" in st.session_state and st.session_state["analysis_result"]:
            result = st.session_state["analysis_result"]
            st.markdown("---")
            st.markdown(f'<p class="page-info">Dianalisis pada {datetime.now().strftime("%d/%m/%Y %H:%M")}</p>', unsafe_allow_html=True)
            rows = render_results(result)

            st.markdown("---")
            report_text = build_download_text(result, rows)
            col_dl, col_new = st.columns([3, 1])
            with col_dl:
                st.download_button(
                    "Download Laporan (.txt)",
                    data=report_text,
                    file_name=f"laporan_pertek_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
                    mime="text/plain",
                    use_container_width=True,
                )
            with col_new:
                if st.button("Analisis Baru", use_container_width=True, key="clear_bottom"):
                    st.session_state["clear_files"] += 1
                    st.session_state.pop("analysis_result", None)
                    st.rerun()

    elif not uploaded_files:
        # Tampilkan hasil terakhir jika ada
        if "analysis_result" in st.session_state and st.session_state["analysis_result"]:
            result = st.session_state["analysis_result"]
            st.info("Menampilkan hasil analisis terakhir. Upload file baru untuk analisis baru.")
            rows = render_results(result)
            st.markdown("---")
            report_text = build_download_text(result, rows)
            st.download_button(
                "Download Laporan (.txt)",
                data=report_text,
                file_name=f"laporan_pertek_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
                mime="text/plain",
                use_container_width=True,
            )
        else:
            st.markdown("")
            col_a, col_b, col_c = st.columns(3)
            with col_a:
                st.markdown("**1. Upload**")
                st.caption("Upload file PDF PI dan Pertek (bisa lebih dari 1 file)")
            with col_b:
                st.markdown("**2. Analisis**")
                st.caption("Klik tombol Analisis & Cocokkan")
            with col_c:
                st.markdown("**3. Hasil**")
            st.caption("Lihat hasil + download laporan")

# ──────────────────────────────────────────────
# TAB 2: REKAP DATA
# ──────────────────────────────────────────────
with tab_rekap:
    render_rekap_tab()

# Footer
st.markdown("---")
st.caption("Pertek Checker v5.0 | Data diproses secara aman.")
