const { Model } = require('objection');

class Category extends Model {
  static get tableName() {
    return 'product_categories'; // Corresponds to the table name in your database
  }
}

module.exports = Category;
