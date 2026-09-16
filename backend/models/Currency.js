const { Model } = require('objection');

class Currency extends Model {
  static get tableName() {
    return 'currency'; // Corresponds to the table name in your database
  }
}

module.exports = Currency;
