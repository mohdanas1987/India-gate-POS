const { Model } = require('objection');

class User extends Model {
  static get tableName() {
    return 'users'; // Corresponds to the table name in your database
  }
}

module.exports = User;
