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
def extract_pdf_text(uploaded_file, max_chars=60000, max_pages=150):
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

                tables = page.extract_tables()
                if tables:
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
SYSTEM_PROMPT = """Anda adalah analis verifikasi dokumen impor di PT Sucofindo. Tugas Anda adalah mencocokkan dan menganalisis dokumen PI (Persetujuan Impor) dan Pertek (Persetujuan Teknis) dari Kementerian Perindustrian.

ATURAN PENTING:
- Output HANYA JSON valid, tanpa markdown code block.
- JANGAN narasi panjang, cukup kalimat singkat.
- Saat membandingkan spesifikasi/uraian barang: jika MAKNA/ISI SAMA tapi beda format penulisan, urutan kata, singkatan, atau bahasa → ANGGAP SESUAI.
- Hanya anggap Tidak Sesuai jika ada perbedaan SUBSTANSIAL (beda HS code, beda jumlah angka, beda negara, beda jenis barang).
- Untuk items barang: HANYA tampilkan item yang TIDAK SESUAI. Jangan tampilkan item yang sesuai.
- Baca SELURUH halaman dokumen dengan teliti, termasuk halaman-halaman lampiran dan tabel.

Struktur JSON:

{
  "info": {
    "nomor_pi": "...",
    "tanggal_pi": "...",
    "nomor_pertek": "...",
    "tanggal_pertek": "...",
    "jenis_api": "API-P" atau "API-U"
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
    "items_tidak_sesuai": [
      {
        "hs_code": "...",
        "uraian": "...",
        "perbedaan": "singkat dan spesifik: 'Jumlah: PI=500 ton, Pertek=400 ton'"
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

  "rekap_data": {
    "daftar_hs": ["list SEMUA HS code yang ada di dokumen, misal: 7213.91.30, 7225.50.90"],
    "negara_muat": ["list semua negara muat, misal: China, India"],
    "pelabuhan_tujuan": ["list semua pelabuhan tujuan, misal: Tanjung Priok, Tanjung Perak"]
  },

  "kesimpulan": {
    "status": "DAPAT DIPROSES" atau "TIDAK DAPAT DIPROSES",
    "catatan": "1-2 kalimat singkat rekomendasi atau alasan jika tidak dapat diproses.",
    "ketidaksesuaian": ["list singkat aspek yg tidak sesuai, kosong jika semua sesuai"]
  }
}

PENTING:
- Untuk VPTI/LS, periksa pengecualian:
  * Impor ke KPBPB, KEK, atau TPB
  * Fasilitas KITE Pembebasan
  * API-P industri otomotif, elektronika, galangan kapal, mould & dies, pesawat terbang, atau alat berat
  * API-P berstatus AEO atau MITA Kepabeanan
  * API-P pengguna SKVI USDFS
  * Penerima fasilitas BMDTP
  * Kontraktor KKS Migas, Kontrak Karya, atau proyek ketenagalistrikan
  * Barang HS 7213.91.30, 7213.91.90, 7213.99.90 (C > 0,6%), 7225.50.90 (TMBP)
- Jika data tidak tersedia, tulis "Tidak tersedia"
- INGAT: beda format/urutan penulisan BUKAN berarti Tidak Sesuai. Fokus pada isi/makna.
- JANGAN cocokkan Negara Muat. Kolom Negara Muat hanya ada di PI, TIDAK ada di Pertek. Jadi JANGAN pernah menandai item sebagai "Tidak Sesuai" karena perbedaan negara muat. Yang dicocokkan HANYA Pelabuhan Tujuan.
- WAJIB isi rekap_data dengan SEMUA HS code, negara muat (dari PI), dan pelabuhan tujuan yang ada di dokumen."""


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
def call_gemini(api_key, model, prompt_text):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt_text}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 65536,
            "responseMimeType": "application/json",
        }
    }
    return requests.post(url, json=payload, timeout=240)


def analyze_documents(api_key, pdf_texts):
    prompt_text = SYSTEM_PROMPT + "\n\n" + build_user_prompt(pdf_texts)
    models = ["gemini-2.0-flash", "gemini-2.5-flash"]
    last_error = ""

    for model in models:
        for attempt in range(4):
            try:
                response = call_gemini(api_key, model, prompt_text)
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

    # Info bar
    st.markdown(f"""<div class="info-bar">
        <strong>PI:</strong> {info.get('nomor_pi', '-')} ({info.get('tanggal_pi', '-')})
        &nbsp;&nbsp;|&nbsp;&nbsp;
        <strong>Pertek:</strong> {info.get('nomor_pertek', '-')} ({info.get('tanggal_pertek', '-')})
        &nbsp;&nbsp;|&nbsp;&nbsp;
        <strong>Jenis:</strong> {info.get('jenis_api', '-')}
    </div>""", unsafe_allow_html=True)

    # 1. Identitas Perusahaan
    id_data = result.get("identitas_perusahaan", {})
    st.markdown('<div class="section-header">1. Identitas Perusahaan</div>', unsafe_allow_html=True)
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
    st.markdown(f'<div class="section-header">2. Spesifikasi Barang &nbsp;<span style="font-weight:normal;color:#64748b;">({total_ok}/{total} sesuai)</span></div>', unsafe_allow_html=True)

    tidak_sesuai_items = spec_data.get("items_tidak_sesuai", [])
    if tidak_sesuai_items:
        html = '<table class="detail-table">'
        html += '<tr><th>HS Code</th><th>Uraian</th><th>Perbedaan</th></tr>'
        for item in tidak_sesuai_items:
            html += f'<tr><td><strong>{item.get("hs_code", "")}</strong></td><td>{item.get("uraian", "")}</td><td style="color:#991b1b;">{item.get("perbedaan", "")}</td></tr>'
        html += '</table>'
        st.markdown(html, unsafe_allow_html=True)
    else:
        if total > 0:
            st.success(f"Seluruh {total} item sesuai")

    # 3. Data PI vs Pertek
    pi_data = result.get("data_pi_vs_pertek", {})
    st.markdown('<div class="section-header">3. Data PI vs Pertek</div>', unsafe_allow_html=True)
    pi_items = pi_data.get("items", [])
    if pi_items:
        html = '<table class="detail-table">'
        html += '<tr><th>Aspek</th><th>PI</th><th>Pertek</th><th>Status</th></tr>'
        for item in pi_items:
            html += f'<tr><td>{item.get("aspek", "")}</td><td>{item.get("pi", "")}</td><td>{item.get("pertek", "")}</td><td>{status_badge(item.get("status", ""))}</td></tr>'
        html += '</table>'
        st.markdown(html, unsafe_allow_html=True)

    # 4. VPTI/LS
    vpti_data = result.get("vpti_ls", {})
    st.markdown('<div class="section-header">4. Analisis VPTI/LS</div>', unsafe_allow_html=True)

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
    rows.append(("Profil usaha", vpti_data.get("profil_usaha", "N/A"), ""))
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

    if "DAPAT" in status_str and "TIDAK" not in status_str:
        html = f'<div class="conclusion-card conclusion-dapat">'
        html += f'<strong style="color:#166534;font-size:1.1rem;">&#9989; {status_str}</strong>'
        if catatan:
            html += f'<br><span style="color:#15803d;font-size:0.9rem;">Catatan: {catatan}</span>'
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

    vpti_data = result.get("vpti_ls", {})
    lines.append("4. ANALISIS VPTI/LS")
    lines.append(f"   Jenis API: {vpti_data.get('jenis_api', '')}")
    lines.append(f"   KBLI: {vpti_data.get('kbli', '')} - {vpti_data.get('kbli_deskripsi', '')}")
    lines.append(f"   Wajib: {'Ya' if vpti_data.get('wajib', True) else 'Tidak'}")
    lines.append(f"   Alasan: {vpti_data.get('alasan_singkat', '')}")
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

        large_files = [p for p in pdf_texts if p["pages"] > 150]
        if large_files:
            names = ", ".join([f"**{p['name']}** ({p['pages']} hal)" for p in large_files])
            st.warning(f"File besar terdeteksi: {names}. Hanya 150 halaman pertama yang dibaca. Data penting PI/Pertek biasanya ada di halaman awal, jadi hasil analisis tetap akurat.")

        with st.expander("Lihat teks yang diekstrak dari PDF", expanded=False):
            for item in pdf_texts:
                st.markdown(f"**{item['name']}** ({item['pages']} halaman)")
                st.text(item["text"][:8000] + ("..." if len(item["text"]) > 8000 else ""))
                st.divider()

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
                    st.session_state["analysis_result"] = result
                    if save_to_sheets(result):
                        st.toast("Data tersimpan ke rekap", icon="✅")
                    else:
                        st.toast("Gagal simpan ke rekap (cek konfigurasi Google Sheets)", icon="⚠️")
                except json.JSONDecodeError:
                    st.error("Error parsing hasil analisis. Coba klik Analisis lagi.")
                except requests.exceptions.HTTPError as e:
                    sc = e.response.status_code if e.response else 0
                    if sc in (401, 403):
                        st.error("API Key tidak valid. Hubungi administrator.")
                    elif sc == 429:
                        st.error("Rate limit tercapai. Tunggu 1 menit lalu coba lagi.")
                    elif sc >= 500:
                        st.error("Server sedang sibuk. Tunggu beberapa detik lalu coba lagi.")
                    else:
                        st.error(f"Terjadi error (kode {sc}). Coba lagi.")
                except ValueError as e:
                    st.error(str(e))
                except Exception:
                    st.error("Terjadi error. Coba lagi dalam beberapa detik.")

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
