import posting_runner as p
text='<b>Тест</b>\n\nТекст поста.\n\n#BORIS #авито #авитолог #объявления #автозагрузка #продажи #автоматизация'
out=p._vk_edit_plain_text(text)
assert out.startswith('Тест\n\nТекст поста.')
assert out.endswith('#BORIS #авито #авитолог #объявления #автозагрузка #продажи #автоматизация')
assert len(p._extract_public_hashtags(out))==7
print('VK_EDIT_HASHTAGS_PRESERVED=PASS')
