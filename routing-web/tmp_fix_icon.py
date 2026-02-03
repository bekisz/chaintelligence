from PIL import Image
import os

INPUT_PATH = "/Users/szablocsbeki/.gemini/antigravity/brain/ef44a17f-ad2c-46cb-9551-20cbfdc00b9b/brain_icon_black_bg_1770159564576.png"
OUTPUT_PATH = "/Users/szablocsbeki/.gemini/antigravity/brain/ef44a17f-ad2c-46cb-9551-20cbfdc00b9b/brain_icon_final_transparent.png"

def remove_bg():
    if not os.path.exists(INPUT_PATH):
        print("Input file not found")
        return

    img = Image.open(INPUT_PATH).convert("RGBA")
    datas = img.getdata()

    new_data = []
    for item in datas:
        r, g, b, a = item
        # Simple intensity test. 
        # Black background is usually (0,0,0) or very dark
        # Neon lines are bright.
        if (r + g + b) < 40: # Threshold for black
             new_data.append((0, 0, 0, 0))
        else:
             # Keep original
             new_data.append(item)

    img.putdata(new_data)
    img.save(OUTPUT_PATH, "PNG")
    print(f"Saved transparent image to {OUTPUT_PATH}")

if __name__ == "__main__":
    remove_bg()
