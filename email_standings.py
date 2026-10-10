import smtplib
from email.message import EmailMessage
import argparse
from typing import NamedTuple, Any
import calculate_standings
import json
from datetime import datetime
import pandas as pd

EmailSettings = NamedTuple("EmailSettings", [("address", str), ("password", str), ("recipients", list[str]), ("smtp", str), ("port", int)])

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-i", "--input", help="Input sheet. It should have the following columns: Name, Number, Position, Team", default=calculate_standings.PICKS_SHEET)
    parser.add_argument("-o", "--output", help="Output sheet to publish standings to.", default=calculate_standings.OUTPUT_SHEET)
    parser.add_argument("-s", "--settings", help="Email settings", default="email_settings.json")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose output")
    args = parser.parse_args()
    
    standings = calculate_standings.update_output(args.input, args.output, verbose=args.verbose)
    settings = load_settings(args.settings)
    send(settings, args.output, standings)
    
def load_settings(email_settings_file: str) -> EmailSettings:
    with open(email_settings_file) as f:
        contents = f.read()
        data = json.loads(contents)
        return EmailSettings(**data)
    
def send(settings: EmailSettings, attachment: str, standings: pd.DataFrame | None = None):
    content = f"""
Standings as of {datetime.now().strftime('%Y-%m-%d %H:%M')}:

{standings.to_string(index=False) if standings is not None else ''}
    """
    
    msg = EmailMessage()
    msg['Subject'] = f"Prisoners' Dilemma Hockey Pool Standings Update"
    msg['From'] = settings.address
    msg['To'] = settings.recipients
    msg.set_content(content)
    with open(attachment, 'rb') as f:
        msg.add_attachment(f.read(), maintype='text', subtype='plain', filename=attachment)

    with smtplib.SMTP_SSL(settings.smtp, settings.port) as server:
        server.login(settings.address, settings.password)
        server.send_message(msg)

if __name__ == "__main__":
    main()