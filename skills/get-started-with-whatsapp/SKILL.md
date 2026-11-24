---
name: get-started-with-whatsapp
description: First steps with WhatsApp through Turbo Notify. Use when the person has just installed the plugin, asks how to begin, wants to know whether their WhatsApp number is connected, or a tool answers that the number is not connected.
---

# Get started with WhatsApp

Use the `turbo-notify` tools and answer in the language the person writes in.

1. Call `list_numbers`, then `get_number` for the number they mean (`main` unless they name another).
2. If the status is `connected`, go to step 4.
3. If it is not connected, do not send and do not retry a send. Say what the status means for them:
   - `no_main_number` or `pending`: no phone is linked yet.
   - `revoked`: WhatsApp ended the link. Either the device was removed under Linked devices on the phone, or the phone went more than 14 days without being used.
   - `failed`: the last linking attempt did not finish.
   - `disconnected` or `inactive`: the link exists. Call `connect_number`, then `get_number` again until it says `connected`.
   - `connecting`, `reconnecting` or `pairing`: wait a few seconds and check again.
   For the first three, a person holding the phone links it again from the Numbers page of the dashboard (https://dashboard.turbonotify.com/numbers) by scanning a QR code in WhatsApp, under Linked devices. No tool here can do that. Ask them to tell you when it is done, then check again.
4. Offer a first test: a short message to their own number with `send_text`. Afterwards call `get_message_status` and tell them whether it was delivered. A send is accepted first and delivered after, so never say "delivered" before the status says so.
5. Finish with two or three things they can ask next. Messages and contacts show up from the moment the number is linked, as they arrive, so a new number starts empty. One more fact: the text of received messages is kept for a short time (one hour by default), so questions about recent messages work best. To keep the number linked, open WhatsApp on the phone at least every 14 days.
