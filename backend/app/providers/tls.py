"""TLS context construction for outbound QRadar connections.

Verification is mandatory everywhere in this codebase; nothing here can turn it
off. What this module exists to do is build the context *explicitly* so the two
things a private-PKI SIEM deployment actually needs — trusting an internal CA,
and tolerating a non-compliant appliance certificate — are narrow, named and
auditable rather than someone reaching for `verify=False` at 2am.
"""

from __future__ import annotations

import logging
import ssl

logger = logging.getLogger("app.providers.tls")


def build_ssl_context(
    ca_bundle: str | None = None,
    *,
    allow_missing_aki: bool = False,
) -> ssl.SSLContext:
    """Build a verifying SSL context, optionally trusting a private CA bundle.

    `ca_bundle` should contain the full trust path the appliance does not send
    itself. QRadar presents only its leaf certificate, so a bundle holding just
    the intermediate fails with "unable to get issuer certificate" — root and
    intermediate must both be present.

    `allow_missing_aki` clears `VERIFY_X509_STRICT`. Read the note below before
    setting it.
    """
    # `create_default_context` gives CERT_REQUIRED, check_hostname=True, a
    # modern cipher suite and the system trust store. Python 3.13+ also sets
    # VERIFY_X509_STRICT and VERIFY_X509_PARTIAL_CHAIN there; 3.12 does not.
    # Set both explicitly so the posture is the same on every interpreter
    # (the image runs 3.12, dev and the lab validations ran newer). After that
    # we only ever subtract from it in the one documented way below.
    context = ssl.create_default_context(cafile=ca_bundle)
    context.verify_flags |= ssl.VERIFY_X509_STRICT | ssl.VERIFY_X509_PARTIAL_CHAIN

    if allow_missing_aki:
        # VERIFY_X509_STRICT, set above on every interpreter, enforces RFC 5280
        # §4.2.1.1: every non-self-signed certificate must carry an Authority
        # Key Identifier. QRadar's self-generated
        # console certificate ships a Subject Key Identifier but no AKI, so
        # strict mode rejects a chain that OpenSSL's own CLI accepts. The
        # failure surfaces as "Missing Authority Key Identifier".
        #
        # Clearing this flag relaxes an RFC *compliance assertion about how the
        # certificate is labelled*. It does not relax the trust decision:
        # signature chain validation to the pinned CA, expiry checking and
        # hostname/IP-SAN matching all still apply, and are still enforced
        # below. This is categorically different from verify_ssl=False, which
        # this codebase refuses outright.
        #
        # The correct fix is to reissue the QRadar certificate with an AKI;
        # this flag is the interoperability escape hatch until that happens.
        context.verify_flags &= ~ssl.VERIFY_X509_STRICT
        logger.warning(
            "TLS: RFC 5280 strict validation relaxed (appliance certificate lacks an "
            "Authority Key Identifier). Chain, expiry and hostname verification remain enforced."
        )

    # Belt and braces: if a future edit above ever weakened these, fail loudly
    # here rather than silently connecting without verification.
    if context.verify_mode is not ssl.CERT_REQUIRED or not context.check_hostname:
        raise RuntimeError("Refusing to build an SSL context that does not verify the peer")

    return context
