# Extra CA certificates for the image build

Some networks inspect HTTPS traffic and re-sign it with their own certificate authority (CA). The
host trusts that CA, but the build container does not, so `pip install` fails with
`CERTIFICATE_VERIFY_FAILED`.

If that happens, put your network's CA bundle here as **`certs/ca-bundle.crt`** and rebuild. The
build then uses it for `pip`. On Debian, where the CA is already installed on the host:

```bash
cp /etc/ssl/certs/ca-certificates.crt certs/ca-bundle.crt
```

`certs/*.crt` is git-ignored, so the file stays on your machine. Without it the build behaves as
before. The running container never needs it: SMOS makes no outgoing HTTPS calls.
