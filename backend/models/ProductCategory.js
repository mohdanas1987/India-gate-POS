const { Model } = require('objection');

class ProductCategory extends Model {
    static get tableName() {
        return 'product_categories'; // Corresponds to the table name in your database
    }
    static get relationMappings() {
        const Product = require('./Product');
        return {
            products: {
                relation: Model.HasManyRelation,
                modelClass: Product,
                join: {
                    from: 'product_categories.id',
                    to: 'products.category_id',
                },
            },
        };
    }
}

module.exports = ProductCategory;
