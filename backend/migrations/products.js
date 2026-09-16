const knex = require('knex');

exports.up = async function (knex) {
    // Create the `users` table
    await knex.schema.createTable('products', (table) => {
        table.increments('id').primary(); // Auto-incrementing ID
        table.string('name').notNullable().index();
        table.string('image').nullable();
        table.bigInteger('category_id').nullable().index();
        table.string('type').nullable();
        table.string('price').notNullable().index();
        table.string('weight').nullable();
        table.string('unit').nullable();
        table.string('quantity').defaultTo(20).nullable().index();
        table.boolean('pos').defaultTo(true);
        table.longText('sales_desc').nullable();
        table.string('tax').nullable();
        table.string('code').nullable().index();
    });

};

exports.down = async function (knex) {
    // Drop the `users` table
    await knex.schema.dropTableIfExists('products');
};

       