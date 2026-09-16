const { Model } = require('objection');

class CashierSession extends Model {
  static get tableName() {
    return 'cashier_sessions'; // Corresponds to the table name in your database
  }
}

module.exports = CashierSession;
