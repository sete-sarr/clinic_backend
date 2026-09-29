"""Filtre de gabarit `money` : {{ invoice.total_amount|money:invoice.currency }} (common/currency.py)."""

from django import template

from common.currency import format_money

register = template.Library()


@register.filter
def money(amount, currency):
    return format_money(amount, currency)
