---
name: send-whatsapp-messages
description: Send WhatsApp messages through Turbo Notify: text, photos, documents, locations, contact cards or calendar invitations. Use when the person asks to message someone, notify a customer or send the same notice to several people.
---

# Send WhatsApp messages

1. Before sending, say which number it goes from, to whom and the exact text. Send after a clear yes, unless the person already gave the recipient and the text and asked you to send now.
2. For several recipients, show how many people and the text, and ask for a yes. Offer to send a test to their own number first. Send only to people who expect to hear from the business. Stop at the first refusal and report it.
3. A send is accepted first and delivered after. Say "sent" only after `get_message_status` agrees, and say "accepted, not yet confirmed" until then.
4. If the answer is `number_not_connected`, stop: the number is offline (see the get-started skill). If it mentions a quota or a plan, stop and say so: the quota is per number and resets with the billing cycle, so asking again changes nothing. Never send the same text again to "make sure".
5. Keep every send small and plain. Do not add promotions, links or claims the person did not write.
6. If the person asks to send later or on a schedule: Turbo Notify has no scheduling of its own. If this chat can create a scheduled task, say plainly that when the time comes ChatGPT still asks the person to allow the send, so it does not go out if nobody is there to allow it. Never say it will be sent automatically. For a message that must leave without anyone present, suggest an automation (n8n, Make or the API), and offer to send it now.
