const { Model } = require('objection');

class Report extends Model {
  static get tableName() {
    return 'reports'; // Corresponds to the table name in your database
  }
}

module.exports = Report;
