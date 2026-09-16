const { Model } = require('objection');

class Note extends Model {
  static get tableName() {
    return 'notes'; // Corresponds to the table name in your database
  }
}

module.exports = Note;
