const { Model } = require('objection');

class Customer extends Model {
  static get tableName() {
    return 'customers'; // Corresponds to the table name in your database
  }
}

module.exports = Customer;
