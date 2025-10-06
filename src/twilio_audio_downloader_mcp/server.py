#!/usr/bin/env python3
"""
Twilio Audio Downloader MCP Server

A Model Context Protocol server that downloads audio files from Twilio URLs
with authentication support and returns them as data streams.
"""

import argparse
import os
import tempfile
import requests
from urllib.parse import urlparse
from pathlib import Path
from typing import Optional, Dict, Any
import traceback
import logging
import sys
import base64
from mcp.server.fastmcp import FastMCP
from mcp.types import BlobResourceContents, EmbeddedResource
from pydantic import BaseModel, FileUrl
from pydantic_settings import BaseSettings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('twilio_audio_downloader.log')
    ]
)
logger = logging.getLogger(__name__)

# Create the MCP server instance
mcp = FastMCP("Twilio Audio Downloader MCP Server")


class TwilioConfig(BaseSettings):
    """Configuration for Twilio authentication and server settings."""

    # Server settings
    host: str = "localhost"
    port: int = 8080
    log_level: str = "INFO"

    # Twilio authentication
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_base_url: str = "https://api.twilio.com"

    # Additional authentication for other services
    auth_credentials: Dict[str, str] = {}

    class Config:
        env_file = ".env"
        env_prefix = "TWILIO_"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._load_auth_from_env()

    def _load_auth_from_env(self):
        """Load authentication credentials from environment variables."""
        # Format: AUTH_<IDENTIFIER>=base_url|username:password
        for key, value in os.environ.items():
            if key.startswith('AUTH_'):
                if '|' in value:
                    base_url_part, auth_part = value.split('|', 1)
                    if ':' in auth_part:
                        username, password = auth_part.split(':', 1)
                        parsed_base = urlparse(base_url_part)
                        if parsed_base.netloc:
                            self.auth_credentials[parsed_base.netloc.lower()] = {
                                'username': username,
                                'password': password,
                                'base_url': base_url_part
                            }


# Global configuration
config = TwilioConfig()


class AudioDownloadResponse(BaseModel):
    """Response model for audio download."""
    success: bool
    data: Optional[str] = None  # Base64 encoded audio data
    filename: str = ""
    content_type: str = ""
    size_bytes: int = 0
    error_message: str = ""


def get_file_extension_from_content_type(content_type: str) -> str:
    """Get appropriate file extension from HTTP Content-Type header."""
    content_type = content_type.lower().split(';')[0].strip()

    extension_map = {
        'audio/wav': '.wav',
        'audio/x-wav': '.wav',
        'audio/wave': '.wav',
        'audio/mpeg': '.mp3',
        'audio/mp3': '.mp3',
        'audio/mp4': '.m4a',
        'audio/m4a': '.m4a',
        'audio/aac': '.aac',
        'audio/ogg': '.ogg',
        'audio/flac': '.flac',
        'audio/webm': '.webm',
        'audio/3gpp': '.3gp',
        'audio/amr': '.amr',
    }

    return extension_map.get(content_type, '.bin')


def get_auth_for_url(url: str) -> Optional[tuple]:
    """Get authentication credentials for a given URL."""
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()

    # Check for Twilio URLs first
    if 'twilio.com' in netloc and config.twilio_account_sid and config.twilio_auth_token:
        logger.info("Using Twilio authentication")
        return (config.twilio_account_sid, config.twilio_auth_token)

    # Check for other configured credentials
    if netloc in config.auth_credentials:
        cred = config.auth_credentials[netloc]
        logger.info(f"Using configured authentication for {netloc}")
        return (cred['username'], cred['password'])

    return None


@mcp.tool()
def download_twilio_audio(url: str) -> Dict[str, Any]:
    """
    Download audio file from Twilio URL with authentication support.

    Args:
        url (str): Twilio audio URL to download from

    Returns:
        Dict[str, Any]: Response containing success status, base64 encoded audio data,
                       filename, content type, size, and error message if failed
    """
    try:
        logger.info(f"Starting audio download from URL: {url}")

        # Validate URL format
        if not url.startswith(('http://', 'https://')):
            error_msg = "Only HTTP and HTTPS URLs are supported"
            logger.error(f"{error_msg}. Received URL: {url}")
            return AudioDownloadResponse(
                success=False,
                error_message=error_msg
            ).dict()

        # Validate URL structure
        try:
            parsed_url = urlparse(url)
            if not parsed_url.netloc:
                error_msg = f"Invalid URL format: {url}"
                logger.error(error_msg)
                return AudioDownloadResponse(
                    success=False,
                    error_message=error_msg
                ).dict()
        except Exception as e:
            error_msg = f"Failed to parse URL {url}: {e}"
            logger.error(error_msg)
            return AudioDownloadResponse(
                success=False,
                error_message=error_msg
            ).dict()

        # Get authentication for this URL
        auth = get_auth_for_url(url)
        if auth:
            logger.info(f"Using authentication for URL: {url}")
        else:
            logger.warning(f"No authentication configured for URL: {url}")

        # Download with authentication if available
        logger.info(f"Initiating HTTP request to: {url}")
        response = requests.get(url, auth=auth, stream=True, timeout=30)
        response.raise_for_status()

        logger.info(f"HTTP response status: {response.status_code}")
        content_type = response.headers.get('content-type', 'application/octet-stream')
        content_length = response.headers.get('content-length', 'unknown')
        logger.info(f"Content type: {content_type}")
        logger.info(f"Content length: {content_length}")

        # Determine file extension from content type
        file_extension = get_file_extension_from_content_type(content_type)
        filename = f"twilio_audio_{parsed_url.path.split('/')[-1]}{file_extension}"

        logger.info(f"Determined filename: {filename}")

        # Read all content into memory
        audio_data = b""
        bytes_downloaded = 0

        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                audio_data += chunk
                bytes_downloaded += len(chunk)

        logger.info(f"Successfully downloaded {bytes_downloaded} bytes")

        if len(audio_data) == 0:
            error_msg = "Downloaded file is empty"
            logger.error(error_msg)
            return AudioDownloadResponse(
                success=False,
                error_message=error_msg
            ).dict()

        # Encode audio data as base64 for JSON transport
        encoded_data = base64.b64encode(audio_data).decode('utf-8')

        logger.info(f"Successfully encoded {len(audio_data)} bytes as base64")

        # return AudioDownloadResponse(
        #     success=True,
        #     data=encoded_data,
        #     filename=filename,
        #     content_type=content_type,
        #     size_bytes=len(audio_data)
        # ).dict()

        blob = BlobResourceContents(
            uri=FileUrl(f"file://{filename}"),
            blob=encoded_data,
            mimeType=content_type
        )

        return EmbeddedResource(
            type="resource",
            resource=blob
        ).dict()

    except requests.exceptions.RequestException as e:
        error_msg = f"HTTP request failed for {url}: {e}"
        logger.error(error_msg)
        logger.error(f"Full traceback: {traceback.format_exc()}")
        return AudioDownloadResponse(
            success=False,
            error_message=error_msg
        ).dict()
    except Exception as e:
        error_msg = f"Unexpected error downloading audio from {url}: {e}"
        logger.error(error_msg)
        logger.error(f"Full traceback: {traceback.format_exc()}")
        return AudioDownloadResponse(
            success=False,
            error_message=error_msg
        ).dict()


@mcp.tool()
def get_server_config() -> Dict[str, Any]:
    """
    Get current server configuration (excluding sensitive credentials).

    Returns:
        Dict[str, Any]: Server configuration information
    """
    return {
        "server_name": "Twilio Audio Downloader MCP Server",
        "version": "0.1.0",
        "host": config.host,
        "port": config.port,
        "log_level": config.log_level,
        "twilio_configured": bool(config.twilio_account_sid and config.twilio_auth_token),
        "additional_auth_domains": list(config.auth_credentials.keys()),
        "supported_protocols": ["http", "https"],
        "supported_audio_formats": ["wav", "mp3", "m4a", "aac", "ogg", "flac", "webm", "3gp", "amr"]
    }


def setup_health_endpoint():
    """Set up health check endpoint for the FastAPI app."""
    try:
        app = mcp.streamable_http_app()

        @app.get("/health")
        async def health_check():
            """Health check endpoint for Docker and monitoring systems."""
            from datetime import datetime
            return {
                "status": "healthy",
                "service": "Twilio Audio Downloader MCP Server",
                "version": "0.1.0",
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "tools": ["download_twilio_audio", "get_server_config"]
            }

        logger.info("Health check endpoint configured at /health")
        return app
    except Exception as e:
        logger.warning(f"Could not set up health endpoint: {e}")
        return None


def main():
    """Main entry point for the server."""
    parser = argparse.ArgumentParser(description="Twilio Audio Downloader MCP Server")
    parser.add_argument(
        "--port",
        type=int,
        default=config.port,
        help=f"Port to run the server on (default: {config.port})"
    )
    parser.add_argument(
        "--host",
        type=str,
        default=config.host,
        help=f"Host to bind the server to (default: {config.host})"
    )

    args = parser.parse_args()

    # Update config with command line arguments
    config.host = args.host
    config.port = args.port

    print(f"Starting Twilio Audio Downloader MCP Server on http://{args.host}:{args.port}")
    print(f"MCP endpoint will be available at: http://{args.host}:{args.port}/mcp")

    if config.twilio_account_sid and config.twilio_auth_token:
        print("✓ Twilio authentication configured")
    else:
        print("⚠ Twilio authentication not configured - set TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN")

    if config.auth_credentials:
        print(f"✓ Additional authentication configured for: {', '.join(config.auth_credentials.keys())}")

    import uvicorn

    # Set up health endpoint and get the app
    app = setup_health_endpoint()
    if app is None:
        app = mcp.streamable_http_app()

    # Run with uvicorn
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level=config.log_level.lower()
    )


if __name__ == "__main__":
    main()