from __future__ import unicode_literals
import frappe
from frappe import _
import erpnext
from frappe.utils import flt, round_based_on_smallest_currency_fraction, money_in_words
from erpnext.controllers.taxes_and_totals import get_itemised_tax
from six import iteritems

def update_itemised_tax_data(doc):
	if not doc.taxes: return

	itemised_tax = get_itemised_tax(doc.taxes)

	for row in doc.items:
		tax_rate = 0.0
		item_tax_rate = 0.0

		if row.item_tax_rate:
			item_tax_rate = frappe.parse_json(row.item_tax_rate)

		# First check if tax rate is present
		# If not then look up in item_wise_tax_detail
		if item_tax_rate:
			for account, rate in iteritems(item_tax_rate):
				tax_rate += rate
		elif row.item_code and itemised_tax.get(row.item_code):
			tax_rate = sum([tax.get('tax_rate', 0) for d, tax in itemised_tax.get(row.item_code).items()])

		row.tax_rate = flt(tax_rate, row.precision("tax_rate"))
		row.tax_amount = flt((row.net_amount * tax_rate) / 100, row.precision("net_amount"))
		row.total_amount = flt((row.net_amount + row.tax_amount), row.precision("total_amount"))

def get_account_currency(account):
	"""Helper function to get account currency."""
	if not account:
		return
	def generator():
		account_currency, company = frappe.get_cached_value("Account", account, ["account_currency", "company"])
		if not account_currency:
			account_currency = frappe.get_cached_value('Company',  company,  "default_currency")

		return account_currency

	return frappe.local_cache("account_currency", account, generator)

def get_tax_accounts(company):
	"""Get the list of tax accounts for a specific company."""
	tax_accounts_dict = frappe._dict()
	tax_accounts_list = frappe.get_all("UAE VAT Account",
		filters={"parent": company},
		fields=["Account"]
		)

	if not tax_accounts_list and not frappe.flags.in_test:
		frappe.throw(_(f'Please set Vat Accounts for Company: "{company}" in UAE VAT Settings'))
	for d in tax_accounts_list:
		for key, name in d.items():
			tax_accounts_dict[name] = name

	return tax_accounts_dict

def update_grand_total_for_rcm(doc, method):
	"""If the Reverse Charge is Applicable subtract the tax amount from the grand total and update in the form."""
	country = frappe.get_cached_value('Company', doc.company, 'country')

	if country != 'United Arab Emirates':
		return

	if not doc.total_taxes_and_charges:
		return

	if doc.reverse_charge == 'Y':
		tax_accounts = get_tax_accounts(doc.company)

		base_vat_tax = 0
		vat_tax = 0

		for tax in doc.get('taxes'):
			if tax.category not in ("Total", "Valuation and Total"):
				continue

			if flt(tax.base_tax_amount_after_discount_amount) and tax.account_head in tax_accounts:
				base_vat_tax += tax.base_tax_amount_after_discount_amount
				vat_tax += tax.tax_amount_after_discount_amount

		doc.taxes_and_charges_added -= vat_tax
		doc.total_taxes_and_charges -= vat_tax
		doc.base_taxes_and_charges_added -= base_vat_tax
		doc.base_total_taxes_and_charges -= base_vat_tax

		update_totals(vat_tax, base_vat_tax, doc)

def update_totals(vat_tax, base_vat_tax, doc):
	"""Update the grand total values in the form."""
	doc.base_grand_total -= base_vat_tax
	doc.grand_total -= vat_tax

	if doc.meta.get_field("rounded_total"):

		if doc.is_rounded_total_disabled():
			doc.outstanding_amount = doc.grand_total

		else:
			doc.rounded_total = round_based_on_smallest_currency_fraction(doc.grand_total,
				doc.currency, doc.precision("rounded_total"))
			doc.rounding_adjustment = flt(doc.rounded_total - doc.grand_total,
				doc.precision("rounding_adjustment"))
			doc.outstanding_amount = doc.rounded_total or doc.grand_total

	doc.in_words = money_in_words(doc.grand_total, doc.currency)
	doc.base_in_words = money_in_words(doc.base_grand_total, erpnext.get_company_currency(doc.company))
	doc.set_payment_schedule()

def make_regional_gl_entries(gl_entries, doc):
	"""Hooked to make_regional_gl_entries in Purchase Invoice.It appends the region specific general ledger entries to the list of GL Entries."""
	country = frappe.get_cached_value('Company', doc.company, 'country')

	if country != 'United Arab Emirates':
		return gl_entries

	if doc.reverse_charge == 'Y':
		tax_accounts = get_tax_accounts(doc.company)
		for tax in doc.get('taxes'):
			if tax.category not in ("Total", "Valuation and Total"):
				continue

			dr_or_cr = "credit" if tax.add_deduct_tax == "Add" else "debit"
			if flt(tax.base_tax_amount_after_discount_amount)  and tax.account_head in tax_accounts:
				account_currency = get_account_currency(tax.account_head)

				gl_entries.append(doc.get_gl_dict(
					{
						"account": tax.account_head,
						"cost_center": tax.cost_center,
						"posting_date": doc.posting_date,
						"against": doc.supplier,
						dr_or_cr: tax.base_tax_amount_after_discount_amount,
						dr_or_cr + "_in_account_currency": tax.base_tax_amount_after_discount_amount \
							if account_currency==doc.company_currency \
							else tax.tax_amount_after_discount_amount
					}, account_currency, item=tax)
				)
	return gl_entries

def validate_returns(doc, method):
	"""Standard Rated expenses should not be set when Reverse Charge Applicable is set."""
	country = frappe.get_cached_value('Company', doc.company, 'country')
	print("-"*50)
	print(doc.reverse_charge)
	print(flt(doc.standard_rated_expenses))
	if country != 'United Arab Emirates':
		return

	if doc.reverse_charge == 'Y' and  flt(doc.standard_rated_expenses) != 0:
		frappe.throw(_("Standard Rated expenses should not be set when Reverse Charge Applicable is Y"))