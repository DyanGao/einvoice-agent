"""A stand-in for the company's ERP system.

It is a separate service with its own database on purpose: the invoice agent
may only reach suppliers, orders, deliveries and bank transactions the way it
would reach a real ERP, over an API. It holds no ground truth about which
invoice belongs to what; that stays with the eval.
"""
