import streamlit as st
import pdfplumber
import json
import io
import os
import time
import requests
from datetime import datetime

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
    .summary-table { width: 100%; border-collapse: collapse; margin: 1rem 0; }
    .summary-table th, .summary-table td {
        border: 1px solid #e5e7eb; padding: 10px 14px; text-align: left;
    }
    .summary-table th { background: #f9fafb; font-weight: 600; }
    .detail-table { width: 100%; border-collapse: collapse; margin: 0.5rem 0; }
    .detail-table th, .detail-table td {
        border: 1px solid #e5e7eb; padding: 8px 12px; text-align: left; font-size: 0.9rem;
    }
    .detail-table th { background: #f3f4f6; font-weight: 600; }
    .block-container { max-width: 1100px; }
    .stAlert p { font-size: 0.95rem; }
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
        return True  # No password set, skip

    st.markdown("### Masukkan Password")
    password = st.text_input("Password", type="password", key="password_input")
    if st.button("Masuk", type="primary"):
        if password == correct_password:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Password salah.")
    return False


# ============================================================
# API KEY MANAGEMENT
# ============================================================
def load_api_key():
    """Load API key from Streamlit secrets or session state."""
    try:
        return st.secrets["GEMINI_API_KEY"]
    except Exception:
        pass
    return st.session_state.get("saved_api_key", "")


# ============================================================
# PDF EXTRACTION
# ============================================================
def extract_pdf_text(uploaded_file):
    """Extract all text from uploaded PDF."""
    text = ""
    try:
        file_bytes = uploaded_file.read()
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for i, page in enumerate(pdf.pages):
                page_text = page.extract_text()
                if page_text:
                    text += f"--- Halaman {i+1} ---\n{page_text}\n\n"
        uploaded_file.seek(0)
    except Exception as e:
        text = f"[Error membaca PDF: {e}]"
    return text


# ============================================================
# ANALYSIS PROMPT
# ============================================================
SYSTEM_PROMPT = """Anda adalah analis verifikasi dokumen impor di PT Sucofindo. Tugas Anda adalah mencocokkan dan menganalisis dokumen PI (Persetujuan Impor) dan Pertek (Persetujuan Teknis) dari Kementerian Perindustrian.

ATURAN OUTPUT:
- Output HANYA JSON valid, tanpa markdown code block, tanpa teks tambahan.
- JANGAN gunakan narasi/paragraf panjang. Gunakan kalimat singkat dan to the point.
- Untuk perbandingan item barang, berikan data per item dalam array (tabel).

Struktur JSON yang HARUS diikuti:

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
    "items": [
      {
        "hs_code": "...",
        "uraian": "...",
        "spesifikasi_pi": "...",
        "spesifikasi_pertek": "...",
        "jumlah_pi": "...",
        "jumlah_pertek": "...",
        "satuan": "...",
        "negara_muat_pi": "...",
        "negara_muat_pertek": "...",
        "pelabuhan_pi": "...",
        "pelabuhan_pertek": "...",
        "status": "Sesuai" atau "Tidak Sesuai",
        "catatan": "singkat, hanya jika ada ketidaksesuaian"
      }
    ],
    "ringkasan": {
      "hs_code": {"status": "Sesuai", "keterangan": ""},
      "uraian_barang": {"status": "Sesuai", "keterangan": ""},
      "spesifikasi_teknis": {"status": "Sesuai", "keterangan": ""},
      "jumlah_satuan": {"status": "Sesuai", "keterangan": "jika Tidak Sesuai, jelaskan singkat: misal 'HS 7208.27.19: PI=500 ton, Pertek=400 ton'"},
      "negara_muat": {"status": "Sesuai", "keterangan": ""},
      "pelabuhan_tujuan": {"status": "Sesuai", "keterangan": ""}
    }
  },

  "data_pi_vs_pertek": {
    "status": "Sesuai" atau "Tidak Sesuai",
    "items": [
      {"aspek": "Nomor Pertek di PI", "pi": "...", "pertek": "...", "status": "Sesuai"},
      {"aspek": "Tanggal Pertek di PI", "pi": "...", "pertek": "...", "status": "Sesuai"},
      {"aspek": "Komoditas", "pi": "sesuai Pertek", "pertek": "-", "status": "Sesuai"}
    ]
  },

  "vpti_ls": {
    "jenis_api": "API-P" atau "API-U",
    "kbli": "...",
    "kbli_deskripsi": "...",
    "wajib": true atau false,
    "alasan_singkat": "1 kalimat alasan",
    "pengecualian": ["list pengecualian yang berlaku, kosong jika tidak ada"],
    "profil_usaha": "1 kalimat deskripsi"
  },

  "kesimpulan": {
    "dapat_ditindaklanjuti": true atau false,
    "ringkasan": "1-2 kalimat singkat",
    "ketidaksesuaian": ["list aspek yang tidak sesuai, kosong jika semua sesuai"]
  }
}

PENTING:
- Analisis SEMUA aspek secara menyeluruh.
- Untuk VPTI/LS, periksa pengecualian:
  * Impor ke KPBPB, KEK, atau TPB
  * Fasilitas KITE Pembebasan
  * API-P industri otomotif, elektronika, galangan kapal, mould & dies, pesawat terbang, atau alat berat
  * API-P berstatus AEO atau MITA Kepabeanan
  * API-P pengguna SKVI USDFS
  * Penerima fasilitas BMDTP
  * Kontraktor KKS Migas, Kontrak Karya, atau proyek ketenagalistrikan
  * Barang HS 7213.91.30, 7213.91.90, 7213.99.90 (C > 0,6%), 7225.50.90 (TMBP)
- Jika data tidak tersedia di dokumen, tulis "Tidak tersedia"
- JANGAN buat narasi panjang, cukup poin-poin singkat"""


def build_user_prompt(pdf_texts):
    """Build the user prompt with all PDF texts."""
    prompt = "Berikut dokumen-dokumen yang perlu dianalisis:\n\n"
    for item in pdf_texts:
        prompt += f"{'='*60}\n"
        prompt += f"DOKUMEN: {item['name']}\n"
        prompt += f"{'='*60}\n"
        prompt += item["text"] + "\n\n"

    prompt += "Identifikasi mana PI dan mana Pertek, lalu cocokkan. Output JSON saja."
    return prompt


# ============================================================
# CALL GEMINI API
# ============================================================
def analyze_documents(api_key, pdf_texts):
    """Send documents to Gemini API for analysis with retry."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": SYSTEM_PROMPT + "\n\n" + build_user_prompt(pdf_texts)}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 65536,
            "responseMimeType": "application/json",
        }
    }

    max_retries = 3
    for attempt in range(max_retries):
        response = requests.post(url, json=payload, timeout=180)

        if response.status_code in (500, 502, 503, 429):
            if attempt < max_retries - 1:
                wait = (attempt + 1) * 5
                time.sleep(wait)
                continue
            else:
                raise ValueError(f"Server sedang sibuk setelah {max_retries} percobaan. Coba lagi nanti.")

        response.raise_for_status()
        break

    data = response.json()

    candidate = data["candidates"][0]
    if candidate.get("finishReason") == "MAX_TOKENS":
        raise ValueError("Response terpotong. Dokumen terlalu panjang, coba upload lebih sedikit file.")

    response_text = candidate["content"]["parts"][0]["text"].strip()

    if response_text.startswith("```"):
        lines = response_text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        response_text = "\n".join(lines)

    return json.loads(response_text)


# ============================================================
# RENDER RESULTS
# ============================================================
def render_results(result):
    """Render the analysis results."""

    info = result.get("info", {})
    st.markdown(f"**PI:** {info.get('nomor_pi', '-')} ({info.get('tanggal_pi', '-')}) | "
                f"**Pertek:** {info.get('nomor_pertek', '-')} ({info.get('tanggal_pertek', '-')}) | "
                f"**Jenis:** {info.get('jenis_api', '-')}")
    st.divider()

    # 1. Identitas Perusahaan
    id_data = result.get("identitas_perusahaan", {})
    st.markdown("#### 1. Identitas Perusahaan")
    id_items = id_data.get("items", [])
    if id_items:
        html = '<table class="detail-table">'
        html += '<tr><th>Aspek</th><th>PI</th><th>Pertek</th><th>Status</th></tr>'
        for item in id_items:
            status = item.get("status", "N/A")
            icon = "&#9989;" if status == "Sesuai" else "&#10060;"
            html += f'<tr><td>{item.get("aspek", "")}</td><td>{item.get("pi", "")}</td><td>{item.get("pertek", "")}</td><td>{icon} {status}</td></tr>'
        html += '</table>'
        st.markdown(html, unsafe_allow_html=True)
    st.markdown("")

    # 2. Spesifikasi Barang (tabel per item)
    spec_data = result.get("spesifikasi_barang", {})
    st.markdown("#### 2. Spesifikasi Barang")
    spec_items = spec_data.get("items", [])
    if spec_items:
        html = '<table class="detail-table">'
        html += '<tr><th>HS Code</th><th>Uraian</th><th>Spec PI</th><th>Spec Pertek</th><th>Jumlah PI</th><th>Jumlah Pertek</th><th>N. Muat PI</th><th>N. Muat Pertek</th><th>Status</th></tr>'
        for item in spec_items:
            status = item.get("status", "N/A")
            icon = "&#9989;" if status == "Sesuai" else "&#10060;"
            html += (f'<tr>'
                     f'<td>{item.get("hs_code", "")}</td>'
                     f'<td>{item.get("uraian", "")}</td>'
                     f'<td>{item.get("spesifikasi_pi", "")}</td>'
                     f'<td>{item.get("spesifikasi_pertek", "")}</td>'
                     f'<td>{item.get("jumlah_pi", "")}</td>'
                     f'<td>{item.get("jumlah_pertek", "")}</td>'
                     f'<td>{item.get("negara_muat_pi", "")}</td>'
                     f'<td>{item.get("negara_muat_pertek", "")}</td>'
                     f'<td>{icon} {status}</td>'
                     f'</tr>')
        html += '</table>'
        st.markdown(html, unsafe_allow_html=True)
    st.markdown("")

    # 3. Data PI vs Pertek (tabel)
    pi_data = result.get("data_pi_vs_pertek", {})
    st.markdown("#### 3. Data PI vs Pertek")
    pi_items = pi_data.get("items", [])
    if pi_items:
        html = '<table class="detail-table">'
        html += '<tr><th>Aspek</th><th>PI</th><th>Pertek</th><th>Status</th></tr>'
        for item in pi_items:
            status = item.get("status", "N/A")
            icon = "&#9989;" if status == "Sesuai" else "&#10060;"
            html += f'<tr><td>{item.get("aspek", "")}</td><td>{item.get("pi", "")}</td><td>{item.get("pertek", "")}</td><td>{icon} {status}</td></tr>'
        html += '</table>'
        st.markdown(html, unsafe_allow_html=True)
    st.markdown("")

    # 4. VPTI/LS
    vpti_data = result.get("vpti_ls", {})
    st.markdown(f"#### 4. Analisis VPTI/LS")
    vpti_items = []
    if vpti_data.get("jenis_api"):
        vpti_items.append(f"- **Jenis API:** {vpti_data['jenis_api']}")
    if vpti_data.get("kbli"):
        vpti_items.append(f"- **KBLI:** {vpti_data['kbli']} - {vpti_data.get('kbli_deskripsi', '')}")
    if vpti_data.get("profil_usaha"):
        vpti_items.append(f"- **Profil:** {vpti_data['profil_usaha']}")
    if vpti_data.get("pengecualian"):
        for p in vpti_data["pengecualian"]:
            vpti_items.append(f"- **Pengecualian:** {p}")
    if vpti_data.get("alasan_singkat"):
        vpti_items.append(f"- **Alasan:** {vpti_data['alasan_singkat']}")
    for line in vpti_items:
        st.markdown(line)

    wajib = vpti_data.get("wajib", True)
    if wajib:
        st.warning("**Wajib VPTI/LS**")
    else:
        st.success("**Tidak wajib VPTI/LS**")

    # ============================================================
    # KESIMPULAN AKHIR
    # ============================================================
    st.divider()
    st.markdown("### Kesimpulan Akhir")

    rows = []
    rows.append(("Identitas perusahaan", id_data.get("status", "N/A"), ""))

    ringkasan = spec_data.get("ringkasan", {})
    for key, label in [
        ("hs_code", "HS Code"),
        ("uraian_barang", "Uraian barang"),
        ("spesifikasi_teknis", "Spesifikasi teknis"),
        ("jumlah_satuan", "Jumlah dan satuan"),
        ("negara_muat", "Negara muat"),
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

    # Build HTML table
    html = '<table class="summary-table">'
    html += '<tr><th style="width:35%">Aspek Pemeriksaan</th><th>Hasil</th></tr>'

    for label, status, keterangan in rows:
        if "Sesuai" in str(status) and "Tidak" not in str(status):
            display = f'&#9989; {status}'
        elif "Tidak Sesuai" in str(status):
            display = f'&#10060; {status}'
            if keterangan:
                display += f'<br><small style="color:#6b7280;">{keterangan}</small>'
        elif "Tidak wajib" in str(status):
            display = f'&#9989; {status}'
        elif "Wajib" in str(status):
            display = f'&#9989; {status}'
        else:
            display = status

        html += f'<tr><td>{label}</td><td>{display}</td></tr>'

    html += '</table>'
    st.markdown(html, unsafe_allow_html=True)

    # Kesimpulan final
    kesimpulan = result.get("kesimpulan", {})
    dapat = kesimpulan.get("dapat_ditindaklanjuti", True)
    ketidaksesuaian = kesimpulan.get("ketidaksesuaian", [])

    if dapat:
        st.success(f"**Dapat ditindaklanjuti.** {kesimpulan.get('ringkasan', '')}")
    else:
        msg = f"**Tidak dapat ditindaklanjuti.**\n\n{kesimpulan.get('ringkasan', '')}"
        if ketidaksesuaian:
            msg += "\n\n**Ketidaksesuaian:**\n"
            for k in ketidaksesuaian:
                msg += f"\n- {k}"
        st.error(msg)

    return rows


def build_download_text(result, rows):
    """Build downloadable text report."""
    lines = []
    lines.append("LAPORAN PENCOCOKAN PI DAN PERTEK")
    lines.append(f"Tanggal: {datetime.now().strftime('%d %B %Y %H:%M')}")
    lines.append("=" * 60)

    info = result.get("info", {})
    lines.append(f"PI: {info.get('nomor_pi', '-')} ({info.get('tanggal_pi', '-')})")
    lines.append(f"Pertek: {info.get('nomor_pertek', '-')} ({info.get('tanggal_pertek', '-')})")
    lines.append(f"Jenis: {info.get('jenis_api', '-')}")
    lines.append("")

    # 1. Identitas
    lines.append("1. IDENTITAS PERUSAHAAN")
    id_data = result.get("identitas_perusahaan", {})
    for item in id_data.get("items", []):
        lines.append(f"   {item.get('aspek', '')}: PI={item.get('pi', '')} | Pertek={item.get('pertek', '')} -> {item.get('status', '')}")
    lines.append("")

    # 2. Spesifikasi
    lines.append("2. SPESIFIKASI BARANG")
    spec_data = result.get("spesifikasi_barang", {})
    for item in spec_data.get("items", []):
        lines.append(f"   HS {item.get('hs_code', '')}: {item.get('uraian', '')} -> {item.get('status', '')}")
        if item.get("catatan"):
            lines.append(f"     Catatan: {item['catatan']}")
    lines.append("")

    # 3. PI vs Pertek
    lines.append("3. DATA PI VS PERTEK")
    pi_data = result.get("data_pi_vs_pertek", {})
    for item in pi_data.get("items", []):
        lines.append(f"   {item.get('aspek', '')}: PI={item.get('pi', '')} | Pertek={item.get('pertek', '')} -> {item.get('status', '')}")
    lines.append("")

    # 4. VPTI/LS
    vpti_data = result.get("vpti_ls", {})
    lines.append("4. ANALISIS VPTI/LS")
    lines.append(f"   Jenis API: {vpti_data.get('jenis_api', '')}")
    lines.append(f"   KBLI: {vpti_data.get('kbli', '')} - {vpti_data.get('kbli_deskripsi', '')}")
    lines.append(f"   Wajib: {'Ya' if vpti_data.get('wajib', True) else 'Tidak'}")
    lines.append(f"   Alasan: {vpti_data.get('alasan_singkat', '')}")
    lines.append("")

    # Kesimpulan
    lines.append("=" * 60)
    lines.append("KESIMPULAN AKHIR")
    lines.append("=" * 60)
    for label, status in rows:
        line = f"  {label}: {status}"
        if keterangan:
            line += f" ({keterangan})"
        lines.append(line)
    lines.append("")

    kesimpulan = result.get("kesimpulan", {})
    dapat = kesimpulan.get("dapat_ditindaklanjuti", True)
    lines.append(f"{'DAPAT' if dapat else 'TIDAK DAPAT'} DITINDAKLANJUTI")
    lines.append(kesimpulan.get("ringkasan", ""))
    ketidaksesuaian = kesimpulan.get("ketidaksesuaian", [])
    if ketidaksesuaian:
        lines.append("Ketidaksesuaian:")
        for k in ketidaksesuaian:
            lines.append(f"  - {k}")

    return "\n".join(lines)


# ============================================================
# MAIN UI
# ============================================================
# Password check
if not check_password():
    st.stop()

st.title("Pertek Checker")
st.caption("Upload PDF PI dan Pertek, otomatis dianalisis dan dicocokkan")

# API Key
api_key = load_api_key()

# Main area
st.markdown("### Upload Dokumen PDF")
st.info("Upload semua file PDF terkait (PI dan Pertek). Bisa lebih dari 1 file.")

# File uploader with clear functionality
if "clear_files" not in st.session_state:
    st.session_state["clear_files"] = 0

uploaded_files = st.file_uploader(
    "Pilih file PDF",
    type=["pdf"],
    accept_multiple_files=True,
    key=f"pdf_uploader_{st.session_state['clear_files']}",
)

if uploaded_files:
    st.success(f"{len(uploaded_files)} file diupload")

    pdf_texts = []
    for f in uploaded_files:
        txt = extract_pdf_text(f)
        pdf_texts.append({"name": f.name, "text": txt})

    with st.expander("Lihat teks yang diekstrak dari PDF", expanded=False):
        for item in pdf_texts:
            st.markdown(f"**{item['name']}**")
            st.text(item["text"][:5000] + ("..." if len(item["text"]) > 5000 else ""))
            st.divider()

    # Buttons
    st.divider()
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
        with st.spinner("Menganalisis dokumen... (biasanya 15-30 detik)"):
            try:
                result = analyze_documents(api_key, pdf_texts)
                st.session_state["analysis_result"] = result
            except json.JSONDecodeError:
                st.error("Error parsing hasil analisis. Coba klik Analisis lagi.")
            except requests.exceptions.HTTPError as e:
                status_code = e.response.status_code if e.response else 0
                if status_code == 401 or status_code == 403:
                    st.error("API Key tidak valid. Hubungi administrator.")
                elif status_code == 429:
                    st.error("Rate limit tercapai. Tunggu 1 menit lalu coba lagi.")
                elif status_code >= 500:
                    st.error("Server sedang sibuk. Tunggu beberapa detik lalu coba lagi.")
                else:
                    st.error(f"Terjadi error (kode {status_code}). Coba lagi.")
            except ValueError as e:
                st.error(str(e))
            except Exception:
                st.error("Terjadi error. Coba lagi dalam beberapa detik.")

    # Show results
    if "analysis_result" in st.session_state and st.session_state["analysis_result"]:
        result = st.session_state["analysis_result"]
        st.divider()
        rows = render_results(result)

        # Download
        st.divider()
        report_text = build_download_text(result, rows)
        st.download_button(
            "Download Laporan (.txt)",
            data=report_text,
            file_name=f"laporan_pertek_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
            mime="text/plain",
            use_container_width=True,
        )

elif not uploaded_files:
    st.markdown("---")
    st.markdown("**Cara pakai:**")
    st.markdown("1. Upload file PDF (PI dan Pertek, bisa lebih dari 1)")
    st.markdown("2. Klik **Analisis & Cocokkan**")
    st.markdown("3. Hasil langsung muncul + bisa download laporan")

# Footer
st.markdown("---")
st.caption("Pertek Checker v3.0 | Data diproses secara aman dan tidak disimpan.")
