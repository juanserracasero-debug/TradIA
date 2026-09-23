import os
import sys
from dotenv import load_dotenv
from src.notifications.email_client import EmailNotifier

load_dotenv()

def main():
    sender = os.getenv('MAILJET_SENDER') or os.getenv('SMTP_SENDER') or os.getenv('GMAIL_ADDRESS')
    recipient = os.getenv('MAILJET_TO') or os.getenv('SMTP_TO') or os.getenv('GMAIL_TO') or sender
    
    if not sender:
        print('ERROR: Remitente (MAILJET_SENDER) no encontrado en .env')
        sys.exit(1)
        
    notifier = EmailNotifier()
    
    if not notifier.is_configured:
        print('ERROR: EmailNotifier reporta que no esta configurado correctamente.')
        sys.exit(1)
        
    print(f'Iniciando prueba de envio SMTP...')
    print(f'Servidor: {notifier.smtp_host}:{notifier.smtp_port}')
    print(f'De: {notifier.sender} -> Para: {notifier.recipient_to}')
    
    subject = 'Prueba Tradia OK'
    message = (
        'Hola Juan,\n\n'
        'Este es un mensaje de prueba generado por el sistema de trading TradIA.\n'
        f'La conexion SMTP TLS a {notifier.smtp_host}:{notifier.smtp_port} se ha establecido correctamente con exito.\n\n'
        'El canal de notificaciones por email esta listo y operativo para recibir alertas de compra/venta y reportes diarios.\n\n'
        'Un saludo,\n'
        'TradIA Bot'
    )
    
    success = notifier.send_alert(message=message, subject=subject)
    
    if success:
        print(f'EXITO: El correo de prueba fue enviado correctamente a {notifier.recipient_to}!')
        sys.exit(0)
    else:
        print('ERROR: Fallo el envio del correo de prueba.')
        sys.exit(1)

if __name__ == '__main__':
    main()
