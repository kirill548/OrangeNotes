"""Generate PUBLIC TEST ONLY TLS identities; never use for real pairing."""
import datetime, json
from pathlib import Path
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
ROOT = Path(__file__).resolve().parents[1]
def name(value): return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,value)])
key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
ca=(x509.CertificateBuilder().subject_name(name('PUBLIC TEST CA')).issuer_name(name('PUBLIC TEST CA')).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(datetime.datetime(2020,1,1)).not_valid_after(datetime.datetime(2050,1,1)).add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True).add_extension(x509.KeyUsage(True,False,False,False,False,True,True,False,False),critical=True).sign(key,hashes.SHA256()))
output={'notice':'PUBLIC TEST PRIVATE KEYS. NEVER TRUST IN PRODUCTION.','ca':ca.public_bytes(serialization.Encoding.PEM).decode()}
for actor in ['a','b','unpaired']:
    leafkey=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    cert=(x509.CertificateBuilder().subject_name(name(actor)).issuer_name(ca.subject).public_key(leafkey.public_key()).serial_number(x509.random_serial_number()).not_valid_before(datetime.datetime(2020,1,1)).not_valid_after(datetime.datetime(2050,1,1)).add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True).add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]),critical=False).add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH,ExtendedKeyUsageOID.CLIENT_AUTH]),critical=False).sign(key,hashes.SHA256()))
    output[actor]={'cert':cert.public_bytes(serialization.Encoding.PEM).decode(),'key':leafkey.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode(),'pin':cert.fingerprint(hashes.SHA256()).hex()}
target=ROOT/'orange_notes_mobile/test/fixtures/PUBLIC_TEST_TLS.json'
target.write_text(json.dumps(output,indent=2)+'\n',encoding='utf-8')
print('Generated PUBLIC TEST ONLY TLS fixtures')
