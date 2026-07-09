import streamlit as st
import pdfplumber
import anthropic
import json
import io
import os
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
    .block-container { max-width: 1100px; }
    .stAlert p { font-size: 0.95rem; }
</style>
""", unsafe_allow_html=True)


# ============================================================
# API KEY MANAGEMENT
# ============================================================
def load_api_key():
    """Load API key from Streamlit secrets or session state."""
    # Try secrets first (for Streamlit Cloud)
    try:
        return st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        pass
    return st.session_state.get("saved_api_key", "")


def save_api_key(key):
    st.session_state["saved_api_key"] = key.strip()


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

Anda HARUS memberikan output dalam format JSON yang valid dengan struktur berikut:

{
  "ringkasan_pembuka": "Paragraf pembuka yang menyebutkan nomor PI, tanggal, nomor Pertek, tanggal, dan bahwa pencocokan telah dilakukan.",

  "identitas_perusahaan": {
    "status": "Sesuai" atau "Tidak Sesuai",
    "nama": "...",
    "nib": "...",
    "alamat": "...",
    "detail": "Penjelasan lengkap..."
  },

  "penandatangan": {
    "status": "Sesuai" atau "Tidak Sesuai" atau "Tidak berlaku (API-P)",
    "detail": "Penjelasan..."
  },

  "spesifikasi_barang": {
    "status": "Sesuai" atau "Tidak Sesuai",
    "hs_code": "Sesuai" atau "Tidak Sesuai",
    "uraian_barang": "Sesuai" atau "Tidak Sesuai",
    "spesifikasi_teknis": "Sesuai" atau "Tidak Sesuai",
    "jumlah_satuan": "Sesuai" atau "Tidak Sesuai",
    "negara_asal": "Sesuai" atau "Tidak Sesuai",
    "negara_muat": "Sesuai" atau "Tidak Sesuai",
    "pelabuhan_tujuan": "Sesuai" atau "Tidak Sesuai",
    "detail": "Penjelasan lengkap termasuk perubahan jika ada..."
  },

  "data_pi_vs_pertek": {
    "status": "Sesuai" atau "Tidak Sesuai",
    "detail": "Penjelasan..."
  },

  "vpti_ls": {
    "jenis_api": "API-P" atau "API-U",
    "kbli": "...",
    "kbli_deskripsi": "...",
    "wajib": true atau false,
    "alasan": "Penjelasan lengkap mengapa wajib/tidak wajib...",
    "pengecualian": ["list pengecualian yang berlaku"],
    "profil_usaha": "Deskripsi profil usaha...",
    "pernyataan_pi": "Apa yang dinyatakan di PI tentang VPTI/LS, jika ada"
  },

  "kesimpulan_akhir": "Paragraf kesimpulan akhir yang merangkum seluruh hasil pencocokan."
}

PENTING:
- Output HANYA JSON, tanpa markdown code block, tanpa teks tambahan di luar JSON.
- Analisis SEMUA aspek berikut secara menyeluruh:
  1. Kesesuaian identitas perusahaan
  2. Kesesuaian penandatangan dokumen rencana distribusi dengan penanggung jawab dalam Pertek (khusus API-U)
  3. Kesesuaian detail spesifikasi barang, HS Code, negara asal, dan informasi teknis lainnya
  4. Kesesuaian data pada PI terakhir dengan Pertek dan dokumen pendukung
  5. Analisis VPTI/LS - tentukan apakah wajib atau pengecualian berdasarkan:
     - Impor ke KPBPB, KEK, atau TPB
     - Impor dengan fasilitas KITE Pembebasan
     - API-P industri otomotif, elektronika, galangan kapal, mould & dies, pesawat terbang, atau alat berat
     - API-P berstatus AEO atau MITA Kepabeanan
     - API-P pengguna SKVI USDFS
     - Perusahaan penerima fasilitas BMDTP
     - Kontraktor KKS Migas, Kontrak Karya, atau proyek ketenagalistrikan
     - Barang dengan HS 7213.91.30, 7213.91.90, 7213.99.90 (C > 0,6%), dan 7225.50.90 (Tin Mill Black Plate)
- Jika informasi tidak tersedia di dokumen, nyatakan "Tidak tersedia dalam dokumen"
- Berikan analisis yang detail dan spesifik berdasarkan data yang ada di dokumen"""


def build_user_prompt(pdf_texts):
    """Build the user prompt with all PDF texts."""
    prompt = "Berikut adalah dokumen-dokumen yang perlu dianalisis dan dicocokkan:\n\n"
    for item in pdf_texts:
        prompt += f"{'='*60}\n"
        prompt += f"DOKUMEN: {item['name']}\n"
        prompt += f"{'='*60}\n"
        prompt += item["text"] + "\n\n"

    prompt += """
Cocokkan seluruh informasi dalam dokumen-dokumen di atas. Identifikasi mana yang merupakan dokumen PI dan mana yang Pertek berdasarkan isinya, lalu lakukan analisis pencocokan lengkap.

Berikan output dalam format JSON sesuai struktur yang diminta."""
    return prompt


# ============================================================
# CALL CLAUDE API
# ============================================================
def analyze_documents(api_key, pdf_texts):
    """Send documents to Claude API for analysis."""
    client = anthropic.Anthropic(api_key=api_key)

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=8000,
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": build_user_prompt(pdf_texts)}
        ],
    )

    response_text = message.content[0].text.strip()

    # Clean up response - remove markdown code blocks if present
    if response_text.startswith("```"):
        lines = response_text.split("\n")
        # Remove first line (```json) and last line (```)
        lines = [l for l in lines if not l.strip().startswith("```")]
        response_text = "\n".join(lines)

    return json.loads(response_text)


# ============================================================
# RENDER RESULTS
# ============================================================
def render_results(result):
    """Render the analysis results with formatting."""

    # Opening summary
    st.markdown("### Hasil Pencocokan")
    st.markdown(result.get("ringkasan_pembuka", ""))
    st.divider()

    # 1. Identitas Perusahaan
    id_data = result.get("identitas_perusahaan", {})
    st.markdown("#### 1. Kesesuaian Identitas Perusahaan")
    status = id_data.get("status", "N/A")
    if status == "Sesuai":
        st.success(f"**{status}**")
    else:
        st.error(f"**{status}**")

    if id_data.get("nama"):
        st.markdown(f"- **Nama:** {id_data['nama']}")
    if id_data.get("nib"):
        st.markdown(f"- **NIB:** {id_data['nib']}")
    if id_data.get("alamat"):
        st.markdown(f"- **Alamat:** {id_data['alamat']}")
    if id_data.get("detail"):
        st.markdown(id_data["detail"])
    st.markdown("")

    # 2. Penandatangan
    pen_data = result.get("penandatangan", {})
    st.markdown("#### 2. Kesesuaian Penandatangan Rencana Distribusi")
    status = pen_data.get("status", "N/A")
    if "Tidak berlaku" in status:
        st.info(status)
    elif status == "Sesuai":
        st.success(f"**{status}**")
    else:
        st.error(f"**{status}**")
    if pen_data.get("detail"):
        st.markdown(pen_data["detail"])
    st.markdown("")

    # 3. Spesifikasi Barang
    spec_data = result.get("spesifikasi_barang", {})
    st.markdown("#### 3. Kesesuaian Spesifikasi Barang, HS Code, dan Informasi Teknis")
    status = spec_data.get("status", "N/A")
    if status == "Sesuai":
        st.success(f"**{status}**")
    else:
        st.error(f"**{status}**")
    if spec_data.get("detail"):
        st.markdown(spec_data["detail"])
    st.markdown("")

    # 4. Data PI vs Pertek
    pi_data = result.get("data_pi_vs_pertek", {})
    st.markdown("#### 4. Kesesuaian Data PI dengan Pertek dan Dokumen Pendukung")
    status = pi_data.get("status", "N/A")
    if status == "Sesuai":
        st.success(f"**{status}**")
    else:
        st.error(f"**{status}**")
    if pi_data.get("detail"):
        st.markdown(pi_data["detail"])
    st.markdown("")

    # 5. VPTI/LS
    vpti_data = result.get("vpti_ls", {})
    st.markdown(f"#### 5. Analisis VPTI/LS ({vpti_data.get('jenis_api', '')})")
    if vpti_data.get("kbli"):
        st.markdown(f"- **KBLI:** {vpti_data['kbli']} - {vpti_data.get('kbli_deskripsi', '')}")
    if vpti_data.get("alasan"):
        st.markdown(vpti_data["alasan"])
    if vpti_data.get("pengecualian"):
        for p in vpti_data["pengecualian"]:
            st.markdown(f"  - {p}")
    if vpti_data.get("pernyataan_pi"):
        st.markdown(f"*Pernyataan di PI: \"{vpti_data['pernyataan_pi']}\"*")

    wajib = vpti_data.get("wajib", True)
    if wajib:
        st.warning("**Kesimpulan VPTI/LS: Wajib dilakukan Verifikasi atau Penelusuran Teknis (VPTI/LS)**")
    else:
        pengecualian_str = ", ".join(vpti_data.get("pengecualian", []))
        st.success(f"**Kesimpulan VPTI/LS: Tidak wajib VPTI/LS** (pengecualian: {pengecualian_str})")

    # ============================================================
    # KESIMPULAN AKHIR TABLE
    # ============================================================
    st.divider()
    st.markdown("### Kesimpulan Akhir")

    rows = []

    # Identitas
    rows.append(("Identitas perusahaan", id_data.get("status", "N/A"), ""))

    # Penandatangan
    pen_status = pen_data.get("status", "N/A")
    pen_detail = pen_data.get("detail", "") if pen_status == "Sesuai" else pen_data.get("detail", "")
    rows.append(("Penandatangan Rencana Distribusi", pen_status, pen_detail))

    # Spec items
    for key, label in [
        ("hs_code", "HS Code"),
        ("uraian_barang", "Uraian barang"),
        ("spesifikasi_teknis", "Spesifikasi teknis"),
        ("jumlah_satuan", "Jumlah dan satuan"),
        ("negara_asal", "Negara asal"),
        ("negara_muat", "Negara muat"),
        ("pelabuhan_tujuan", "Pelabuhan tujuan"),
    ]:
        rows.append((label, spec_data.get(key, "N/A"), ""))

    # PI vs Pertek
    rows.append(("Data PI vs Pertek dan dokumen pendukung", pi_data.get("status", "N/A"), ""))

    # Profil usaha
    rows.append(("Analisis profil usaha", vpti_data.get("profil_usaha", "N/A"), ""))

    # VPTI
    if wajib:
        vpti_status = "Wajib dilakukan VPTI/LS"
    else:
        vpti_status = "Tidak wajib VPTI/LS"
    rows.append(("Kewajiban VPTI/LS", vpti_status, ""))

    # Build HTML table
    html = '<table class="summary-table">'
    html += '<tr><th style="width:35%">Aspek Pemeriksaan</th><th>Hasil</th></tr>'

    for label, status, detail in rows:
        if "Tidak berlaku" in status:
            display = f"<em>{status}</em>"
            if detail:
                display += f"<br><small>{detail}</small>"
        elif "Sesuai" in status and "Tidak" not in status:
            display = f'&#9989; <strong>{status}</strong>'
            if detail:
                display += f"<br><small>{detail}</small>"
        elif "Tidak Sesuai" in status:
            display = f'&#10060; <strong>{status}</strong>'
            if detail:
                display += f"<br><small>{detail}</small>"
        elif "Wajib" in status and "Tidak" not in status:
            display = f'&#9989; <strong>{status}</strong>'
        elif "Tidak wajib" in status:
            display = f'&#9989; <strong>{status}</strong>'
        else:
            display = status
            if detail:
                display += f"<br><small>{detail}</small>"

        html += f'<tr><td>{label}</td><td>{display}</td></tr>'

    html += '</table>'
    st.markdown(html, unsafe_allow_html=True)

    # Final conclusion
    st.markdown("### Kesimpulan")
    kesimpulan = result.get("kesimpulan_akhir", "")
    if kesimpulan:
        st.markdown(kesimpulan)

    return rows


def build_download_text(result, rows):
    """Build downloadable text report."""
    lines = []
    lines.append("LAPORAN PENCOCOKAN PI DAN PERTEK")
    lines.append(f"Tanggal: {datetime.now().strftime('%d %B %Y %H:%M')}")
    lines.append("=" * 60)
    lines.append("")
    lines.append(result.get("ringkasan_pembuka", ""))
    lines.append("")

    # 1
    id_data = result.get("identitas_perusahaan", {})
    lines.append("1. Kesesuaian identitas perusahaan")
    lines.append(f"   Status: {id_data.get('status', 'N/A')}")
    if id_data.get("nama"):
        lines.append(f"   Nama: {id_data['nama']}")
    if id_data.get("nib"):
        lines.append(f"   NIB: {id_data['nib']}")
    if id_data.get("alamat"):
        lines.append(f"   Alamat: {id_data['alamat']}")
    if id_data.get("detail"):
        lines.append(f"   {id_data['detail']}")
    lines.append("")

    # 2
    pen_data = result.get("penandatangan", {})
    lines.append("2. Kesesuaian penandatangan dokumen rencana distribusi")
    lines.append(f"   Status: {pen_data.get('status', 'N/A')}")
    if pen_data.get("detail"):
        lines.append(f"   {pen_data['detail']}")
    lines.append("")

    # 3
    spec_data = result.get("spesifikasi_barang", {})
    lines.append("3. Kesesuaian spesifikasi barang, HS Code, dan informasi teknis")
    lines.append(f"   Status keseluruhan: {spec_data.get('status', 'N/A')}")
    for key, label in [
        ("hs_code", "HS Code"), ("spesifikasi_teknis", "Spesifikasi teknis"),
        ("jumlah_satuan", "Jumlah & satuan"), ("negara_asal", "Negara asal"),
        ("negara_muat", "Negara muat"), ("pelabuhan_tujuan", "Pelabuhan tujuan"),
    ]:
        lines.append(f"   {label}: {spec_data.get(key, 'N/A')}")
    if spec_data.get("detail"):
        lines.append(f"   {spec_data['detail']}")
    lines.append("")

    # 4
    pi_data = result.get("data_pi_vs_pertek", {})
    lines.append("4. Kesesuaian data PI dengan Pertek")
    lines.append(f"   Status: {pi_data.get('status', 'N/A')}")
    if pi_data.get("detail"):
        lines.append(f"   {pi_data['detail']}")
    lines.append("")

    # 5
    vpti_data = result.get("vpti_ls", {})
    lines.append(f"5. Analisis VPTI/LS ({vpti_data.get('jenis_api', '')})")
    if vpti_data.get("kbli"):
        lines.append(f"   KBLI: {vpti_data['kbli']} - {vpti_data.get('kbli_deskripsi', '')}")
    if vpti_data.get("alasan"):
        lines.append(f"   {vpti_data['alasan']}")
    wajib = vpti_data.get("wajib", True)
    lines.append(f"   Kesimpulan: {'Wajib VPTI/LS' if wajib else 'Tidak wajib VPTI/LS'}")
    lines.append("")

    # Summary table
    lines.append("=" * 60)
    lines.append("KESIMPULAN AKHIR")
    lines.append("=" * 60)
    for label, status, detail in rows:
        line = f"  {label}: {status}"
        if detail:
            line += f" - {detail}"
        lines.append(line)
    lines.append("")

    # Final
    lines.append("-" * 60)
    lines.append(result.get("kesimpulan_akhir", ""))

    return "\n".join(lines)


# ============================================================
# MAIN UI
# ============================================================
st.title("Pertek Checker")
st.caption("Upload PDF PI dan Pertek → otomatis dianalisis dan dicocokkan")

# Sidebar: API Key
with st.sidebar:
    st.markdown("### Pengaturan")
    saved_key = load_api_key()
    api_key = st.text_input(
        "Claude API Key",
        value=saved_key,
        type="password",
        help="Dapatkan di console.anthropic.com. Key disimpan lokal di komputer Anda.",
    )
    if api_key and api_key != saved_key:
        save_api_key(api_key)
        st.success("API key tersimpan!")

    st.divider()
    st.caption("Data Anda diproses langsung via API key Anda sendiri. Tidak ada data yang disimpan di server lain.")

# Main area
st.markdown("### Upload Dokumen PDF")
st.info("Upload semua file PDF terkait (PI dan Pertek). Bisa lebih dari 1 file. Sistem akan otomatis mengenali mana PI dan mana Pertek.")

uploaded_files = st.file_uploader(
    "Pilih file PDF",
    type=["pdf"],
    accept_multiple_files=True,
    key="pdf_uploader",
)

if uploaded_files:
    st.success(f"{len(uploaded_files)} file diupload")

    # Show extracted text in expanders
    pdf_texts = []
    for f in uploaded_files:
        txt = extract_pdf_text(f)
        pdf_texts.append({"name": f.name, "text": txt})

    with st.expander("Lihat teks yang diekstrak dari PDF", expanded=False):
        for item in pdf_texts:
            st.markdown(f"**{item['name']}**")
            st.text(item["text"][:5000] + ("..." if len(item["text"]) > 5000 else ""))
            st.divider()

    # Analyze button
    st.divider()
    if st.button("Analisis & Cocokkan", type="primary", use_container_width=True, disabled=not api_key):
        if not api_key:
            st.error("Masukkan Claude API Key di sidebar terlebih dahulu.")
        else:
            with st.spinner("Menganalisis dokumen... (biasanya 15-30 detik)"):
                try:
                    result = analyze_documents(api_key, pdf_texts)
                    st.session_state["analysis_result"] = result
                except json.JSONDecodeError as e:
                    st.error(f"Error parsing hasil analisis. Coba lagi.")
                    st.exception(e)
                except anthropic.AuthenticationError:
                    st.error("API Key tidak valid. Periksa kembali di sidebar.")
                except anthropic.RateLimitError:
                    st.error("Rate limit tercapai. Tunggu beberapa detik lalu coba lagi.")
                except Exception as e:
                    st.error(f"Error: {e}")

    # Show results if available
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
    st.markdown("1. Masukkan Claude API Key di sidebar (sekali saja, otomatis tersimpan)")
    st.markdown("2. Upload file PDF (PI dan Pertek, bisa lebih dari 1)")
    st.markdown("3. Klik **Analisis & Cocokkan**")
    st.markdown("4. Hasil langsung muncul + bisa download laporan")


# Footer
st.markdown("---")
st.caption("Pertek Checker v2.0 | 100% lokal & privat - data diproses via API key Anda sendiri")
