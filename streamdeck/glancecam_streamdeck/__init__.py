"""GlanceCam Stream Deck controller.

A physical Elgato Stream Deck turned into a camera wall: each key shows a live
thumbnail of one camera and a press picks that camera to show full screen on the
attached kiosk display. The controller talks only to the GlanceCam app over HTTP
(the camera list and the server-side snapshot proxy), so it never needs the
camera credentials itself.
"""

__version__ = "0.1.0"
