import os
import pypdfium2 as pdfium
from PIL import ImageChops, Image

blank_filename = 'letter_blank.jpg'
blank_path = os.path.join("assets", blank_filename)
with Image.open(blank_path) as blank_im:
    card_list = [blank_im.copy()] * 6
    
pdf = pdfium.PdfDocument(f"game/output/port royal_offset.pdf")
n_pages = len(pdf)

# Dimensions of the resized print sheet
print_width = 3300
print_height = 2550

for page_number in range(n_pages):
    page = pdf.get_page(page_number)
    pil_image = page.render(scale = 300/72).to_pil().resize((print_width, print_height))

    card_list.append(pil_image)

pdf_path = os.path.join(f"game/output/port royal_offset_blanks.pdf")
card_list[0].save(pdf_path, save_all=True, append_images=card_list[1:], resolution=300)