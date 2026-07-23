"""Binance Square posting API — text and image posts."""
import asyncio
import logging
import httpx

logger = logging.getLogger(__name__)

BASE_V1 = "https://www.binance.com/bapi/composite/v1/public/pgc/openApi"
BASE_V2 = "https://www.binance.com/bapi/composite/v2/public/pgc/openApi"


def _headers(api_key: str) -> dict:
    return {
        "X-Square-OpenAPI-Key": api_key,
        "Content-Type": "application/json",
        "clienttype": "binanceSkill",
    }


async def upload_image(image_path: str, api_key: str) -> str | None:
    """Upload image to Binance Square via presigned S3 URL.
    
    Returns the public image URL, or None on failure.
    """
    import os
    headers = _headers(api_key)
    filename = os.path.basename(image_path)

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # 1. Get presigned URL
            r = await client.post(f"{BASE_V2}/image/presignedUrl", headers=headers,
                                  json={"imageName": filename})
            data = r.json()
            if data.get("code") != "000000":
                logger.error(f"Presigned URL error: {data.get('message', data)}")
                return None

            presigned_url = data["data"]["presignedUrl"]
            file_ticket = data["data"]["fileTicket"]

            # 2. Upload to S3
            with open(image_path, "rb") as f:
                img_bytes = f.read()

            r = await client.put(presigned_url, content=img_bytes,
                                headers={"Content-Type": "image/png"})
            if r.status_code not in (200, 201):
                logger.error(f"S3 upload failed: {r.status_code}")
                return None

            # 3. Poll image status (max 30s)
            for i in range(10):
                await asyncio.sleep(3)
                r = await client.post(f"{BASE_V2}/image/imageStatus", headers=headers,
                                      json={"fileTicket": file_ticket})
                sdata = r.json()
                if sdata.get("code") != "000000":
                    continue
                status = sdata["data"]["status"]
                if status == 1:
                    image_url = sdata["data"]["imageUrl"]
                    logger.info(f"📸 Image uploaded: {image_url}")
                    return image_url
                elif status == 2:
                    logger.error(f"Image processing failed: {sdata['data'].get('failedReason')}")
                    return None

            logger.error("Image upload timeout (30s)")
            return None

    except Exception as e:
        logger.error(f"Image upload error: {e}")
        return None


async def post_to_square(text: str, api_key: str, image_path: str = None) -> str:
    """Post to Binance Square — text only, or text + image.
    
    Args:
        text: Post text content.
        api_key: Square OpenAPI key.
        image_path: Optional path to PNG image. If provided, uploads and attaches.
    
    Returns:
        Status string (✅ or ❌).
    """
    if not api_key:
        return "❌ SQUARE_API_KEY not set"

    # Upload image if provided
    image_url = None
    if image_path:
        image_url = await upload_image(image_path, api_key)
        if not image_url:
            logger.warning("Image upload failed, posting text-only")

    # Build post body
    body = {"bodyTextOnly": text}
    if image_url:
        body["contentType"] = 1
        body["imageList"] = [image_url]

    headers = _headers(api_key)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{BASE_V1}/content/add", headers=headers, json=body)
            data = resp.json()
            if data.get("code") == "000000":
                post_id = data.get("data", {}).get("id", "unknown")
                img_tag = " 📸" if image_url else ""
                return f"✅ Posted!{img_tag} https://www.binance.com/square/post/{post_id}"
            return f"❌ Square error: {data.get('message', 'unknown')}"
    except Exception as e:
        logger.error(f"Square post error: {e}")
        return f"❌ Connection error: {e}"
