"""
gen_session.py
──────────────
Run this ONCE on your PC to generate a Pyrogram session string.
Paste the output into USER_SESSION in your .env file.

Usage:
    python gen_session.py
"""
# from pyrogram import Client
# from config import Config

# app = Client(
#     "temp_gen",
#     api_id   = Config.API_ID,
#     api_hash = Config.API_HASH,
# )

# with app:
#     session_string = app.export_session_string()
#     print("\n" + "="*60)
#     print("YOUR SESSION STRING (copy everything between the lines):")
#     print("="*60)
#     print(session_string)
#     print("="*60)
#     print("\nPaste this as USER_SESSION=<string> in your .env file")
#     print("Delete temp_gen.session file after copying.\n")
