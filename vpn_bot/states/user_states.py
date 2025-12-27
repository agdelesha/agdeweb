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
