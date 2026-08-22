import cloudinary.uploader


def delete_cloudinary_file(file_field):
    """
    Delete a file from Cloudinary.

    Works with django-cloudinary-storage where
    ImageField.name stores the Cloudinary public_id.
    """
    if not file_field:
        return

    public_id = file_field.name

    if not public_id:
        return

    try:
        result = cloudinary.uploader.destroy(public_id)
        print(result)
    except Exception as e:
        print(f"Cloudinary delete error: {e}")