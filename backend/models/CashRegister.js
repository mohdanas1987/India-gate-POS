const { Model } = require('objection');

class CashRegister extends Model {
  static get tableName() {
    return 'cash_register'; // Corresponds to the table name in your database
  }
}

module.exports = CashRegister;
