import os
import subprocess
from pypdf import PdfReader, PdfWriter

PDF_PATH = "game/output/castle combo_offset.pdf"
# PRINTER_NAME = "Your_Printer_Name"  # Leave as "" to use default
USE_LP = True  # Set to False on Windows

def send_to_printer(temp_pdf_path):
    if USE_LP:
        # macOS/Linux: send as duplex
        # subprocess.run(["lp", "-d", PRINTER_NAME, "-o", "sides=two-sided-long-edge", temp_pdf_path])
    
        subprocess.run(["lp", "-o", "sides=two-sided-long-edge", temp_pdf_path])
    else:
        # Windows basic print command (duplex depends on printer defaults)
        subprocess.run(["print", temp_pdf_path], shell=True)

def print_duplex_pairs(pdf_path):
    reader = PdfReader(pdf_path)
    total_pages = len(reader.pages)

    if total_pages % 2 != 0:
        print("Warning: PDF has an odd number of pages. Last page will be skipped.")

    for i in range(0, total_pages - 1, 2):
        writer = PdfWriter()
        writer.add_page(reader.pages[i])       # Front
        writer.add_page(reader.pages[i + 1])   # Back

        temp_filename = f"temp_duplex_{i//2 + 1}.pdf"
        with open(temp_filename, "wb") as f:
            writer.write(f)

        print(f"Sending sheet {i//2 + 1} (pages {i+1}-{i+2}) to printer...")
        send_to_printer(temp_filename)
        os.remove(temp_filename)

print_duplex_pairs(PDF_PATH)