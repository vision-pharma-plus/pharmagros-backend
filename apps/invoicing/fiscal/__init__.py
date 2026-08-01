"""
OBR electronic invoicing (EBMS).

Burundi's Office Burundais des Recettes requires sales documents to be
declared electronically. Two things follow from that requirement, and this
package is organised around both:

  * Every posted document carries a *fiscal signature* — a deterministic
    identifier computed locally at posting time from the taxpayer's NIF, the
    system identifier the OBR issues on certification, the document date and
    the document number. It is computed here rather than requested from the
    OBR so that a document is fully identified the moment it is issued, even
    with no network.

  * Declaration is *asynchronous*. The counter must not stop because a link
    to Bujumbura is down, so posting never blocks on the network. Documents
    queue and a periodic sweep drains them.

`signature.py`  — building and parsing the fiscal signature
`payload.py`    — mapping an Invoice onto the OBR's declaration schema
`client.py`     — HTTP transport, token handling and request logging
`service.py`    — the submit/cancel operations the rest of the app calls
"""

from __future__ import annotations
