const { Model } = require('objection');

class Tax extends Model {
  static get tableName() {
    return 'taxes'; // Corresponds to the table name in your database
  }
}

module.exports = Tax;
