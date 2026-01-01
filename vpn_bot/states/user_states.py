from aiogram.fsm.state import State, StatesGroup


class RegistrationStates(StatesGroup):
    waiting_for_password = State()
    waiting_for_phone = State()


class PaymentStates(StatesGroup):
    waiting_for_receipt = State()


class ConfigRequestStates(StatesGroup):
    waiting_for_device = State()


class AdminStates(StatesGroup):
    waiting_for_gift_user = State()
    waiting_for_config_user = State()
    waiting_for_new_password = State()
    waiting_for_traffic_threshold = State()
    waiting_for_configs_threshold = State()
    waiting_for_broadcast_all = State()
    waiting_for_broadcast_user = State()
    waiting_for_broadcast_server = State()
    waiting_for_server_data = State()
    waiting_for_server_edit = State()
    waiting_for_channel_name = State()
    waiting_for_max_configs = State()
    waiting_for_user_max_configs = State()
    waiting_for_referral_percent = State()
    waiting_for_default_referral_percent = State()
    # Управление ботами
    waiting_for_bot_token = State()
    waiting_for_bot_password = State()
    waiting_for_bot_channel = State()
    waiting_for_bot_max_configs = State()
    # Управление ценами
    waiting_for_price_trial = State()
    waiting_for_price_30 = State()
    waiting_for_price_90 = State()
    waiting_for_price_180 = State()
    # Управление логами
    waiting_for_log_channel = State()


class WithdrawalStates(StatesGroup):
    waiting_for_bank = State()
    waiting_for_phone = State()
