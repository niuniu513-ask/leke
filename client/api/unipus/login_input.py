"""Replace controlled login inputs and verify the value before submitting."""
from selenium.webdriver.common.keys import Keys


def replace_login_value(driver, element, value):
    value = str(value or '')
    element.click()
    # clear() can leave a React-controlled input's state unchanged. Use the
    # native setter and input event together so the next render retains it.
    driver.execute_script('''
        const input = arguments[0], value = arguments[1];
        const setter = Object.getOwnPropertyDescriptor(
            HTMLInputElement.prototype, 'value').set;
        setter.call(input, value);
        input.dispatchEvent(new Event('input', {bubbles: true}));
        input.dispatchEvent(new Event('change', {bubbles: true}));
    ''', element, value)
    if element.get_property('value') != value:
        element.send_keys(Keys.CONTROL, 'a')
        element.send_keys(Keys.BACKSPACE)
        element.send_keys(value)
    if element.get_property('value') != value:
        raise RuntimeError('登录输入框未正确更新，请在浏览器中手动填写后重试')
