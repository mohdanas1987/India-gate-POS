const { Model } = require('objection');

class Setting extends Model {
  static get tableName() {
    return 'settings'; // Corresponds to the table name in your database
  }
}

module.exports = Setting;
