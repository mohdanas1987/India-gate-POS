exports.up = async function (knex) {
    await knex.schema.table('orders', table => table.bigInteger('cash_register_id').nullable());
};

exports.down = async function (knex) {
    await knex.schema.table('orders', table => table.dropColumn('cash_register_id'));
};

 