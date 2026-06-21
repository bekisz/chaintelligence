import sys
from PIL import Image

def make_transparent(img_path, output_path):
    img = Image.open(img_path)
    img = img.convert("RGBA")
    
    datas = img.getdata()
    
    # We want to detect the background color. 
    # Let's take a sample of the background color from top-left corner
    bg_color = datas[0]
    print(f"Detected background color (RGBA): {bg_color}")
    
    newData = []
    for item in datas:
        # Check if the pixel color is close to the background color.
        # Background is very dark (r, g, b values all less than ~30).
        # We can calculate the distance from bg_color or just filter out very dark pixels.
        # Since it's a glow effect, we can also transition the alpha smoothly!
        # If we just do simple threshold:
        r, g, b, a = item
        # If it's close to the background (which is around 18, 19, 22), we make it transparent.
        # Let's calculate the distance to bg_color (e.g., Euclidean distance in RGB space).
        dist = ((r - bg_color[0])**2 + (g - bg_color[1])**2 + (b - bg_color[2])**2)**0.5
        
        if dist < 35:
            # Fully transparent
            newData.append((0, 0, 0, 0))
        elif dist < 80:
            # Semi-transparent transition for smooth edges
            factor = (dist - 35) / (80 - 35)
            # Retain the pixel color but reduce alpha
            new_a = int(255 * factor)
            # Since the background was dark, let's boost the brightness a bit to counter the dark blending
            newData.append((r, g, b, new_a))
        else:
            newData.append(item)
            
    img.putdata(newData)
    img.save(output_path, "PNG")
    print(f"Saved transparent image to {output_path}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python make_transparent.py <input> <output>")
        sys.exit(1)
    make_transparent(sys.argv[1], sys.argv[2])
