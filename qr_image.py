import qrcode

# Configure the QR code properties
qr = qrcode.QRCode(
    version=1,  # Controls size (1 is 21x21). Set to None to auto-fit.
    error_correction=qrcode.constants.ERROR_CORRECT_H,  # High error correction (30%)
    box_size=10,  # Pixels per box
    border=4,     # Border thickness (minimum is 4)
)

# Add data
qr.add_data('Hello, World!')
qr.make(fit=True) # fit=True automatically determines the best version

# Create image with custom colors (RGB tuples or color names)
img = qr.make_image(fill_color="darkblue", back_color="white")

img.save("custom_qr.png")