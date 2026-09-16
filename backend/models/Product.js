const { Model } = require('objection');

class Product extends Model {
    static get tableName() {
        return 'products'; // Corresponds to the table name in your database
    }
    static get relationMappings() {
        const ProductCategory = require('./ProductCategory');
        return {
            category: {
                relation: Model.BelongsToOneRelation,
                modelClass: ProductCategory,
                join: {
                    from: 'products.category_id',
                    to: 'product_categories.id',
                },
            },
        };
    }
}

module.exports = Product;
