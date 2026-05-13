import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.crypto import generate_master_key_b64, generate_secret


def main():
    print("# Copy các dòng dưới vào file .env")
    print(f"MASTER_KEY_B64={generate_master_key_b64()}")
    print(f"JWT_SECRET={generate_secret(48)}")
    print(f"ADMIN_API_KEY={generate_secret(32)}")


if __name__ == "__main__":
    main()
