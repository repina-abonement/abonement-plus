import unittest
from crm import perms,server

class OfflinePermissions(unittest.TestCase):
    def test_only_authorized_sections_even_with_extra_flags(self):
        user={'role':'operator','is_offline_admin':1,'is_senior':1,'is_journal':1,'is_promoter':1}
        self.assertEqual(perms.view_of(user),{'sales':'full','members':'full','skips':'full','schedule':'view','inbox':'full','planner':'own','search':'full','staffexpenses':'own','client_sales':'full','client_create':'full','client_ops':'full','merch_stock':'full'})
        self.assertTrue(perms.allowed(user,'client_sales:full'))
        self.assertTrue(perms.allowed(user,'client_create:full'))
        self.assertTrue(perms.allowed(user,'client_ops:full'))
        self.assertTrue(perms.allowed(user,'merch_stock:full'))
        for spec in ('operator','admin','any','coach','merch','journal','schedule:full','payments','settings','export'):
            self.assertFalse(perms.allowed(user,spec),spec)
        for spec in ('sales:full','members:full','skips:full','schedule:view'):
            self.assertTrue(perms.allowed(user,spec),spec)
        self.assertTrue(perms.allowed(user,'inbox'))
        self.assertTrue(perms.allowed(user,'planner'))
        self.assertTrue(perms.allowed(user,'search:full'))
        self.assertFalse(perms.allowed(user,'planner:full'))

    def test_regular_operator_and_owner_unchanged(self):
        self.assertTrue(perms.allowed({'role':'operator'},'operator'))
        self.assertTrue(perms.can({'role':'operator'},'sales'))
        self.assertTrue(perms.allowed({'role':'operator'},'merch_stock:full'))
        self.assertTrue(perms.can({'role':'admin','is_offline_admin':1},'dashboard'))
