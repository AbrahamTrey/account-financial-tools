# -*- coding: utf-8 -*-
##############################################################################
#
#    Author: Nicolas Bessi, Guewen Baconnier
#    Copyright 2012 Camptocamp SA
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################
import logging
from openerp import models, fields, api
from openerp import netsvc

logger = logging.getLogger('credit.control.line.mailing')


class CreditCommunication(models.TransientModel):
    """Shell class used to provide a base model to email template and reporting
    Il use this approche in version 7 a browse record
    will exist even if not saved

    """
    _name = "credit.control.communication"
    _description = "credit control communication"
    _rec_name = 'partner_id'

    partner_id = fields.Many2one('res.partner', 'Partner', required=True)

    current_policy_level = fields.Many2one('credit.control.policy.level',
                                           'Level',
                                           required=True)
    credit_control_line_ids = fields.Many2many('credit.control.line',
                                               rel='comm_credit_rel',
                                               string='Credit Lines')

    @api.model
    def _get_company(self):
        company_obj = self.env['res.company']
        return company_obj._company_default_get('credit.control.policy')

    company_id = fields.Many2one('res.company',
                                 string='Company',
                                 default=_get_company,
                                 required=True)
    user_id = fields.Many2one('res.users',
                              default=lambda self: self.env.user,
                              string='User')

    @api.multi
    def get_email(self):
        """ Return a valid email for customer """
        self.ensure_one()
        contact = self.get_contact_address()
        return contact.email

    @api.multi
    @api.returns('res.partner')
    def get_contact_address(self):
        self.ensure_one()
        partner_obj = self.env['res.partner']
        partner = self.partner_id
        add_ids = partner.address_get(adr_pref=['invoice']) or {}
        add_id = add_ids.get('invoice', add_ids.get('default', False))
        return partner_obj.browse(add_id)

    @api.model
    @api.returns('credit.control.line')
    def _get_credit_lines(self, line_ids, partner_id, level_id):
        """ Return credit lines related to a partner and a policy level """
        cr_line_obj = self.env['credit.control.line']
        cr_lines = cr_line_obj.search([('id', 'in', line_ids),
                                       ('partner_id', '=', partner_id),
                                       ('policy_level_id', '=', level_id)])
        return cr_lines

    @api.model
    def _generate_comm_from_credit_lines(self, lines):
        """ Aggregate credit control line by partner, level, and currency
        It also generate a communication object per aggregation.
        """
        if not lines:
            return []
        comms = self.browse()
        sql = (
            "SELECT distinct partner_id, policy_level_id, "
            " credit_control_line.currency_id, "
            " credit_control_policy_level.level"
            " FROM credit_control_line JOIN credit_control_policy_level "
            "   ON (credit_control_line.policy_level_id = "
            "       credit_control_policy_level.id)"
            " WHERE credit_control_line.id in %s"
            " ORDER by credit_control_policy_level.level, "
            "          credit_control_line.currency_id"
        )
        cr = self._cr
        cr.execute(sql, (tuple(lines.ids), ))
        res = cr.dictfetchall()
        for level_assoc in res:
            data = {}
            level_lines = self._get_credit_lines(lines.ids,
                                                 level_assoc['partner_id'],
                                                 level_assoc['policy_level_id']
                                                 )

            data['credit_control_line_ids'] = [(6, 0, level_lines.ids)]
            data['partner_id'] = level_assoc['partner_id']
            data['current_policy_level'] = level_assoc['policy_level_id']
            comm = self.create(data)
            comms += comm
        return comms

    @api.multi
    @api.returns('mail.mail')
    def _generate_emails(self):
        """ Generate email message using template related to level """
        email_message_obj = self.env['mail.mail']
        email_template_obj = self.pool['email.template']
        att_obj = self.env['ir.attachment']
        emails = email_message_obj.browse()
        required_fields = ['subject',
                           'body_html',
                           'email_from',
                           'email_to']
        cr, uid, context = self.env.cr, self.env.uid, self.env.context
        for comm in self:
            template = comm.current_policy_level.email_template_id
            email_values = email_template_obj.generate_email(cr, uid,
                                                             template.id,
                                                             comm.id,
                                                             context=context)
            email_values['body_html'] = email_values['body']
            email_values['type'] = 'email'

            email = email_message_obj.create(email_values)

            state = 'sent'
            # The mail will not be send, however it will be in the pool, in an
            # error state. So we create it, link it with
            # the credit control line
            # and put this latter in a `email_error` state we not that we have
            # a problem with the email
            if not all(email_values.get(field) for field in required_fields):
                state = 'email_error'

            comm.credit_control_line_ids.write({'mail_message_id': email.id,
                                                'state': state})

            attachments = att_obj.browse()
            for att in email_values.get('attachments', []):
                attach_fname = att[0]
                attach_datas = att[1]
                data_attach = {
                    'name': attach_fname,
                    'datas': attach_datas,
                    'datas_fname': attach_fname,
                    'res_model': 'mail.mail',
                    'res_id': email.id,
                    'type': 'binary',
                }
                attachments += att_obj.create(data_attach)
            email.write({'attachment_ids': [(6, 0, attachments.ids)]})
            emails += email
        return emails

    @api.multi
    def _generate_report(self):
        """ Will generate a report by inserting mako template
        of related policy template

        """
        return ''
        service = netsvc.LocalService('report.credit_control_summary')
        cr, uid = self.env.cr, self.env.uid
        result, format = service.create(cr, uid, self.ids, {}, {})
        return result
        # TODO
        # return self.env['report'].get_pdf(self, 'credit_control_summary')

    @api.multi
    @api.returns('credit.control.line')
    def _mark_credit_line_as_sent(self):
        line_obj = self.env['credit.control.line']
        lines = line_obj.browse()
        for comm in self:
            lines += comm.credit_control_line_ids

        lines.write({'state': 'sent'})
        return lines
